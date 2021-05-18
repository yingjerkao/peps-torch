import torch
import math
import numpy as np
import config as cfg
import copy
from collections import OrderedDict
from u1sym.ipeps_u1 import IPEPS_U1SYM
from read_write_SU3_tensors import *
from models import SU3_chiral
from ctm.generic.env import *
from ctm.generic import ctmrg
from ctm.generic import rdm
import json
import unittest
import logging

log = logging.getLogger(__name__)

# parse command line args and build necessary configuration objects
parser = cfg.get_args_parser()
parser.add_argument("--theta", type=float, default=0., help="angle parametrizing the chiral Hamiltonian")
parser.add_argument("--j1", type=float, default=0., help="nearest-neighbor exchange coupling")
parser.add_argument("--j2", type=float, default=0., help="next-nearest-neighbor exchange coupling")
parser.add_argument("--top_freq", type=int, default=-1, help="frequency of transfer operator spectrum evaluation")
parser.add_argument("--top_n", type=int, default=2,
                    help="number of leading eigenvalues of transfer operator to compute")
args, unknown_args = parser.parse_known_args()


def main():
    cfg.configure(args)
    cfg.print_config()
    print('\n')
    torch.set_num_threads(args.omp_cores)
    torch.manual_seed(args.seed)
    t_device = torch.device(args.GLOBALARGS_device)

    # Import all elementary tensors and build initial state
    elementary_tensors = []
    for name in ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'L0', 'L1', 'L2']:
        tens = load_SU3_tensor(name)
        tens = tens.to(t_device)
        if name in ['S0', 'S1', 'S2', 'L2']:
            elementary_tensors.append(1j * tens)
        else:
            elementary_tensors.append(tens)
    # define initial coefficients
    coeffs = {(0, 0): torch.tensor([1.0000,  0.3563,  4.4882, -0.3494, -3.9341, 0., 0., 1.0000, 0.2429, 0.], dtype=torch.float64, device=t_device)} # Ji-yao's ground state for theta=pi/4
    #coeffs = {(0,0): torch.tensor([1.,0.,0.,0.,0.,0.,0.,1.,0.,0.], dtype=torch.float64, device=t_device)} # AKLT state
    #coeffs = {(0, 0): torch.tensor([1.0000, -0.8699,  1.5465,  0.0000,  0.0000,  0.0000,  0.0000,  1.0000,
    #     1.4435,  0.0000], dtype=torch.float64, device=t_device)} # for J1=1.2, no ctmrg convergence
    # define which coefficients will be added a noise
    var_coeffs_allowed = torch.tensor([0, 1, 1, 1, 1, 0, 0, 0, 1, 0], dtype=torch.float64, device=t_device)
    state = IPEPS_U1SYM(elementary_tensors, coeffs, var_coeffs_allowed)
    state.add_noise(args.instate_noise)
    print(f'Current state: {state.coeffs[(0, 0)].data}')

    model = SU3_chiral.SU3_CHIRAL(theta=args.theta, j1=args.j1, j2=args.j2)

    def energy_f(state, env):
        e_dn = model.energy_triangle_dn(state, env, force_cpu=False)
        e_up = model.energy_triangle_up(state, env, force_cpu=False)
        e_nnn = model.energy_nnn(state, env)
        return (e_up + e_dn + e_nnn) / 3

    def ctmrg_conv_energy(state, env, history, ctm_args=cfg.ctm_args):
        if not history:
            history = []
        e_dn = model.energy_triangle_dn(state, env, force_cpu=ctm_args.conv_check_cpu)
        e_up = model.energy_triangle_up(state, env, force_cpu=ctm_args.conv_check_cpu)
        e_nnn = model.energy_nnn(state, env)
        e_curr = (e_up + e_dn + e_nnn)/3
        history.append(e_curr.item())
        print(f'Step n°{len(history)}    E_site ={e_curr.item()}   (E_up={e_up.item()}, E_dn={e_dn.item()})')


        for c_loc,c_ten in env.C.items():
            u,s,v= torch.svd(c_ten, compute_uv=False)
            print(f"\n\nspectrum C[{c_loc}]")
            for i in range(args.chi):
                print(f"{i} {s[i]}")

        if (len(history) > 1 and abs(history[-1] - history[-2]) < ctm_args.ctm_conv_tol) \
                or len(history) >= ctm_args.ctm_max_iter:
            log.info({"history_length": len(history), "history": history})
            return True, history
        return False, history

    ctm_env_init = ENV(args.chi, state)
    init_env(state, ctm_env_init)

    # energy per site
    #e_dn_init = model.energy_triangle_dn(state, ctm_env_init)
    #e_up_init = model.energy_triangle_up(state, ctm_env_init)
    #e_nnn_init = model.energy_nnn(state, ctm_env_init)
    #e_tot_init = (e_dn_init + e_up_init + e_nnn_init)/3
    #print(f'E_up={e_up_init.item()}, E_dn={e_dn_init.item()}, E_tot={e_tot_init.item()}')

    ctm_env_final, *ctm_log = ctmrg.run(state, ctm_env_init, conv_check=ctmrg_conv_energy)

    # energy per site
    e_dn_final = model.energy_triangle_dn(state, ctm_env_final, force_cpu=False)
    e_up_final = model.energy_triangle_up(state, ctm_env_final, force_cpu=False)
    e_nnn_final = model.energy_nnn(state, ctm_env_final)
    e_tot_final = (e_dn_final + e_up_final + e_nnn_final)/3

    # P operators
    P_up = model.P_up(state, ctm_env_final)
    P_dn = model.P_dn(state, ctm_env_final)

    # bond operators
    P23, P13, P12 = model.P_bonds(state, ctm_env_final)

    print(f'\n\n E_up={e_up_final.item()}, E_dn={e_dn_final.item()}, E_tot={e_tot_final.item()}')
    print(f' Re(P_up)={torch.real(P_up).item()}, Im(P_up)={torch.imag(P_up).item()}')
    print(f' Re(P_dn)={torch.real(P_dn).item()}, Im(P_dn)={torch.imag(P_dn).item()}')
    print(f' P_23={P23.item()}, P_13={P13.item()}, P_12={P12.item()}')

    colors3, colors8 = model.eval_lambdas(state, ctm_env_final)
    print(
        f' <Lambda_3> = {torch.real(colors3[0]).item()}, {torch.real(colors3[1]).item()}, {torch.real(colors3[2]).item()}')
    print(
        f' <Lambda_8> = {torch.real(colors8[0]).item()}, {torch.real(colors8[1]).item()}, {torch.real(colors8[2]).item()}')

    # environment diagnostics
    print("\n")
    print("Final environment")
    for c_loc, c_ten in ctm_env_final.C.items():
        u, s, v = torch.svd(c_ten, compute_uv=False)
        print(f"spectrum C[{c_loc}]")
        for i in range(args.chi):
            print(f"{i} {s[i]}")
    print("\n")
            
if __name__ == '__main__':
    if len(unknown_args) > 0:
        print("args not recognized: " + str(unknown_args))
        raise Exception("Unknown command line arguments")
    main()
