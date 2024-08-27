# import context
import torch
# import cytnx
import argparse
import config as cfg
from ipeps.ipeps import *
from ctm.generic.env import *
from ctm.generic import ctmrg

# parse command line args and build necessary configuration objects
parser= cfg.get_args_parser()
parser.add_argument("--tensor", default="TFIM.cytnx", help="Input building block tensor for iPEPS.")
parser.add_argument("--bondim", type=int, default=2)
args, unknown_args = parser.parse_known_args()

def main():
    cfg.configure(args)

    tmp = torch.rand([2,args.bondim,args.bondim,args.bondim,args.bondim], dtype=cfg.global_args.torch_dtype,device=cfg.global_args.device)
    tmp= tmp/tmp.abs().max()
    sites = {(0,0): tmp}
    state = IPEPS(sites)
    
    # def ctmrg_conv_energy(state, env, history, ctm_args=cfg.ctm_args):
    #     return False, history

    ctm_env_init = ENV(args.chi, state)
    init_env(state, ctm_env_init)
    
    # print(", ".join(["epoch","energy"]+obs_labels))
    # print(", ".join([f"{-1}",f"{e_curr0}"]+[f"{v}" for v in obs_values0]))
    
    def ctmrg_conv_C(state2, env, history, ctm_args=cfg.ctm_args):
        if not history:
            history=[]
        old = []
        if (len(history)>0):
            old = history[:4*env.chi]
        new = []
        u,s,v = torch.svd(env.C[((0,0),(-1,-1))])
        for i in range(env.chi):
            new.append(s[i].item())
        u,s,v = torch.svd(env.C[((0,0),(1,-1))])
        for i in range(env.chi):
            new.append(s[i].item())
        u,s,v = torch.svd(env.C[((0,0),(1,-1))])
        for i in range(env.chi):
            new.append(s[i].item())
        u,s,v = torch.svd(env.C[((0,0),(1,1))])
        for i in range(env.chi):
            new.append(s[i].item())

        diff = 0.
        if (len(history)>0):
            for i in range(4*env.chi):
                history[i] = new[i]
                if (abs(old[i]-new[i])>diff):
                    diff = abs(old[i]-new[i])
        else:
            for i in range(4*env.chi):
                history.append(new[i])
        history.append(diff)
        # print("diff={0:<50}".format(diff), end="\r")
        # print(ctm_args.ctm_conv_tol)
        if (len(history[4*env.chi:]) > 1 and diff < ctm_args.ctm_conv_tol)\
            or len(history[4*env.chi:]) >= ctm_args.ctm_max_iter:
            log.info({"history_length": len(history[4*env.chi:]), "history": history[4*env.chi:]})
            print("")
            # print("CTMRG length: "+str(len(history[4*env.chi:])))
            return True, history
        return False, history


    env, history, t_ctm, t_obs = ctmrg.run(state, ctm_env_init, conv_check= ctmrg_conv_C)
    print("t_ctm = ", t_ctm)
    # print("t_obs = ", t_obs)
    # # 6) compute final observables
    # e_curr0 = energy_f(state, ctm_env_init)
    # obs_values0, obs_labels = eval_obs_f(state,ctm_env_init)
    # history, t_ctm, t_obs= ctm_log
    # print("\n")
    # print(", ".join(["epoch","energy"]+obs_labels))
    # print("FINAL "+", ".join([f"{e_curr0}"]+[f"{v}" for v in obs_values0]))
    # print(f"TIMINGS ctm: {t_ctm} conv_check: {t_obs}")

    # path = args.txt
    # with open(path, 'a') as f:
    #     f.write("  ".join([f"{args.h}"]+[f"{e_curr0}"]+[f"{v}" for v in obs_values0]))
    #     f.write("\n")
    #         print(f"{i} {l[i,0]} {l[i,1]}")

if __name__=='__main__':
    # if len(unknown_args)>0:
    #     print("args not recognized: "+str(unknown_args))
    #     raise Exception("Unknown command line arguments")
    main()
