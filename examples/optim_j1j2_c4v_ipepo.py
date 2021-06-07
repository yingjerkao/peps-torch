"""Optimizer for one step of Trotter-Suzuki decomposition. The optimizer
maximizes the ratio of overlaps w.r.t the coefficients of the basic Cs tensors
and updates them."""

import torch
import warnings
try:
    from tqdm import tqdm  # progress bars
    TQDM = True
except ImportError as e:
    TQDM = False
    warnings.warn("tqdm not available", Warning)
import su2sym.thermal_1site_c4v.base_tensors.base_tensor as bt
import su2sym.thermal_1site_c4v.onsite as ons
import optim.ts_lbfgs as ts
from models.j1j2 import J1J2_C4V_BIPARTITE_THERMAL
# peps-torch imports
import config as cfg
from ipeps.ipeps_c4v_thermal import IPEPS_C4V_THERMAL
from ctm.one_site_c4v import ctmrg_c4v
from ctm.one_site_c4v.rdm_c4v_thermal import rdm2x1_sl
from ctm.one_site_c4v.env_c4v import *
import logging
import json
log = logging.getLogger(__name__)

############################ Initialization ##################################
# Get parser from config
parser = cfg.get_args_parser()
parser.add_argument("--j1", type=float, default=1., help="nearest-neighbour coupling")
parser.add_argument("--j2", type=float, default=0., help="next nearest-neighbour coupling")
parser.add_argument("--n", type=int, default=40, help="max number of optimization steps within a single gate application")
# argument --opt_max_iter defines the total number of complete imaginary time steps
parser.add_argument("--tau", type=float, default=1/8, help=r"\tau = \frac{\beta}{N}")
parser.add_argument("--t", type=float, default=1e-4, help="threshold of optimizer")
parser.add_argument("--no", type=float, default=1e-2, help="noise added to the coefficients during single gate application")
# --OPTARGS_lr defines learning rate of the gradient optimizer within single gate application
parser.add_argument("--p", type=int, default=3, help="patience of the convergence")
# --out_prefix defines the prefix for output files generated by simulation
args, unknown_args = parser.parse_known_args()

# Create dictionary containing all the tensors 
base_tensor_dict = bt.base_tensor_dict(args.bond_dim, device=cfg.global_args.device)

# Create dictionary of the parameters
params_j1 = {'a': {'permutation': (0,1,2,3,4), 'new_symmetry': 'Cx'},
             'b': {'permutation': (0,3,4,1,2), 'new_symmetry': 'Cx'},
             'c': {'permutation': (0,2,3,4,1), 'new_symmetry': ''},
             'd': {'permutation': (0,4,1,2,3), 'new_symmetry': 'C4v'}}

params_j2 = {'a': {'permutation': (0,1,2,3,4), 'new_symmetry': 'Cs', 'diag': 'diag'},
             'b': {'permutation': (0,1,2,3,4), 'new_symmetry': 'Cs', 'diag': 'diag'},
             'c': {'permutation': (0,1,2,3,4), 'new_symmetry': '',   'diag': 'off'},
             'd': {'permutation': (0,1,2,3,4), 'new_symmetry': 'C4v','diag': 'off'}}

coeff_ini = {'4': [0.,0.,0.,0.,1.,0.,0.,0.],
             '7': [0.,0.,0.,0.,1.]+[0.]*44}

################################ Main ########################################
def main():
    ### Initialization ###
    # Parse command line arguments and configure simulation parameters
    cfg.configure(args)
    cfg.print_config()
    torch.set_num_threads(args.omp_cores)
    torch.manual_seed(args.seed)

    # `cfg` now holds all parsed command line options (and defaults) 
    params_onsite = {
        'symmetry':'C4v', 'coeff': coeff_ini[f'{args.bond_dim}'],
        'base_tensor_dict': base_tensor_dict, 'bond_dim': args.bond_dim, 
        'dtype':torch.float64, 'device': cfg.global_args.device
    }
    
    # Define convergence criterion on the 2 sites reduced density matrix
    def ctmrg_conv_rdm2x1(state, env, history, ctm_args=cfg.ctm_args):
        if not history:
            history = dict({"log": []})
        # we use specialized rdm2x1_sl for ipepo (rank-6) ansatz while the 
        # `state` is (fused ipepo) ipeps
        _tmp= IPEPS_C4V_THERMAL( state.site().view( 2,2,*state.site().size()[1:] ) )
        rdm2x1 = rdm2x1_sl(_tmp, env, force_cpu=ctm_args.conv_check_cpu)
        dist = float('inf')
        if len(history["log"]) > 1:
            dist = torch.dist(rdm2x1, history["rdm"], p=2).item()
        # update history
        history["rdm"] = rdm2x1
        history["log"].append(dist)
        if dist<ctm_args.ctm_conv_tol:
            log.info({"history_length": len(history['log']), "history": history['log'],
                "final_multiplets": compute_multiplets(ctm_env)})
            return True, history
        elif len(history['log']) >= ctm_args.ctm_max_iter:
            log.info({"history_length": len(history['log']), "history": history['log'],
                "final_multiplets": compute_multiplets(ctm_env)})
            return False, history
        return False, history

    # Define relevant observables to be evaluated at each full step of imag. time evolution
    simulation_history={'labels': None, 'obs': [], 'coeffs': []}
    def obs_fn(state, ctm_env, opt_context):
        epoch= opt_context["epoch"]
        beta= opt_context["beta"]
        e0 = model.energy_1x1(state, ctm_env, force_cpu=False)
        obs_values, obs_labels = model.eval_obs(state,ctm_env,force_cpu=False)
        if epoch==0: 
            print(", ".join(["epoch","beta","e0"]+obs_labels))
            simulation_history['labels']= ", ".join(["epoch","beta","e0"]+obs_labels)
        simulation_history['obs'].append([epoch, beta, e0.item()]+[v.item() for v in obs_values])
        print(", ".join([f"{epoch}",f"{beta}",f"{e0}"]+[f"{v}" for v in obs_values]))

    # Initialize parameters for J1 and J2 term
    gate = ts.build_gate(args.j1, args.tau)
    gate2 = ts.build_gate(args.j2, args.tau)
    # Initialize onsite tensor
    onsite1= ons.OnSiteTensor(params_onsite)
    
    # define model holding the relevant energy and observable evaluation functions
    model= J1J2_C4V_BIPARTITE_THERMAL(j1=args.j1, j2=args.j2)

    # enter imag. time evolution loop
    print("\n\n",end="")
    loop_range= tqdm(range(args.opt_max_iter)) if TQDM else range(args.opt_max_iter)
    for step in loop_range:
        
        # 0) convert ipepo to ipeps
        onsite1.normalize()
        state= IPEPS_C4V_THERMAL(onsite1.site_unfused())
        state_fused= state.to_fused_ipeps_c4v()
        ctm_env = ENV_C4V(args.chi, state_fused)
        # 1a) (optional) reinitialize environment from scratch. Default: True 
        if cfg.opt_args.opt_ctm_reinit:
            init_env(state_fused, ctm_env)
        # 1b) compute environment for 1site C4v symmetric ipepo
        ctm_env, ctm_history, t_ctm, t_conv_check= ctmrg_c4v.run_dl(state_fused, ctm_env, \
            conv_check=ctmrg_conv_rdm2x1, ctm_args=cfg.ctm_args)
        # 1c) store diagonostic information from CTMRG
        log_entry=dict({"id": step, "t_ctm": t_ctm, "t_check": t_conv_check})
        log.info(json.dumps(log_entry))

        # 2) compute observables with converged environment
        obs_fn(state, ctm_env, {"epoch": step, "beta": step*args.tau})
        # 3) Save obs and coeffs for post-processing. At this point the last observables
        #    coincide with last coeff entry
        simulation_history['coeffs'].append( \
            onsite1.coeff if isinstance(onsite1.coeff,list) else onsite1.coeff.tolist() )
        with open(args.out_prefix+"_sim-data.dat", 'w') as outfile:
            json.dump(simulation_history, outfile, indent=4)

        # apply nearest-neighbour gates
        if args.j1 != 0:
            # Apply j1 gate
            for bond_type in ['a','b','c','d']:
                new_symmetry = params_j1[bond_type]['new_symmetry']
                permutation = params_j1[bond_type]['permutation']
                
                # Optimize
                onsite1, loc_h = ts.optimization_2sites(onsite1=onsite1, new_symmetry=new_symmetry,
                            permutation=permutation, env=ctm_env, gate=gate,
                            const_w2=ts.const_w2_2sites, cost_function=ts.cost_function_2sites,
                            noise=args.no, max_iter=args.n, threshold=args.t, patience=args.p,
                            optimizer_class=torch.optim.LBFGS, lr=cfg.opt_args.lr)
                # log number of internal optimizer steps, final cost function, l1-norm of final gradient     
                log.info(f"NN-gate {step} {bond_type} {len(loc_h)} {loc_h[-1]}")

        # apply next nearest-neighbour gates
        if args.j2 != 0:
            # Apply j2 gate
            for bond_type in ['a','b','c','d']:
                new_symmetry = params_j2[bond_type]['new_symmetry']
                permutation = params_j2[bond_type]['permutation']
                diag = params_j2[bond_type]['diag']
                def const_w2(tensor, env, gate):
                    return ts.const_w2_NNN_plaquette(tensor, onsite1.site(), diag, env, gate)
                def cost_function(tensor1, tensor2, env, gate, w2):
                    return ts.cost_function_NNN_plaquette(tensor1, tensor2, onsite1.site(), diag, env, gate, w2)
                
                # Optimize
                onsite1, loc_h = ts.optimization_2sites(onsite1=onsite1, new_symmetry=new_symmetry,
                            permutation=permutation, env=ctm_env, gate=gate2,
                            const_w2=const_w2, cost_function=cost_function,
                            noise=args.no, max_iter=args.n, threshold=args.t, patience=args.p,
                            optimizer_class=torch.optim.LBFGS, lr=cfg.opt_args.lr)
                log.info(f"NNN-gate {step} {bond_type} {len(loc_h)} {loc_h[-1]}")


if __name__ == '__main__':
    main()