#python examples/kagome/optim_spin1_kagome.py --ansatz IPEPS --bond_dim 2 --chi 20 --GLOBALARGS_device cuda
#python optim_spin1_xxz_kagome.py --delta 1 --h 0 --ansatz IPEPS --bond_dim 2 --chi 20 --GLOBALARGS_device cuda:0
python optim_spin1_xxz_kagome.py --delta 1 --h 0 --ansatz IPESS --bond_dim 2 --chi 20 --GLOBALARGS_device cuda:0