import warnings
import torch
from collections import OrderedDict
from itertools import chain
import json
import itertools
import math
import config as cfg
import groups.su2 as su2
from ipeps.tensor_io import *
from ipeps.ipeps import IPEPS, read_ipeps, _write_ipeps_json
import logging
log = logging.getLogger(__name__)

# TODO drop constrain for aux bond dimension to be identical on 
# all bond indices

class IPEPS_1S_Q(IPEPS):
    def __init__(self, sites=None, q=(0,0), 
        peps_args=cfg.peps_args, global_args=cfg.global_args):
        r"""
        :param sites: map from elementary unit cell to on-site tensors
        :param peps_args: ipeps configuration
        :param global_args: global configuration
        :param q: wave-vector
        :type q: tuple(float,float)
        :type sites: dict[tuple(int,int) : torch.tensor]
        :type peps_args: PEPSARGS
        :type global_args: GLOBALARGS

        Member ``sites`` is a dictionary of non-equivalent on-site tensors
        indexed by tuple of coordinates (x,y) within the elementary unit cell.
        The index-position convetion for on-site tensors is defined as follows::

               u s 
               |/ 
            l--a--r  <=> a[s,u,l,d,r]
               |
               d
        
        where s denotes physical index, and u,l,d,r label four principal directions
        up, left, down, right in anti-clockwise order starting from up. 
        IPEPS_1S_Q expects single tensor in ``sites`` dictionary.

        This iPEPS assumes in-plane order with wave-vector q, generated by applying 
        a unitary to physical index of on-site tensor defined as::

            u = exp(-i 2\pi q \cdot r) 
            
            # generating tiling
        
            # y\x -2 -1 0 1 2
            # -2   au(-2q_x-2q_y) au(-q_x-2q_y) au(-2q_y) au(q_x-2q_y) au(2q_x-2q_y)
            # -1   au(-2q_x-q_y)  au(-q_x-q_y)  au(-q_y)  au(q_x-q_y)  au(2q_x-q_y)
            #  0   au(-2q_x)      au(-q_x)      a         au(q_x)      au(2q_x)
            #  1   au(-2q_x+q_y)  au(-q_x+q_y)  au(q_y)   au(q_x+q_y)  au(2q_x+q_y)

        """
        if sites:
            assert len(sites)==1,"Too many sites"
        self.q= q if type(q)==torch.Tensor else torch.as_tensor(q,dtype=torch.float64)
        super().__init__(sites, lX=1, lY=1, peps_args=peps_args, global_args=global_args)

    def get_parameters(self):
        r"""
        :return: variational parameters of iPEPS
        :rtype: iterable
        
        This function is called by optimizer to access variational parameters of the state.
        """
        return list(self.sites.values())+[self.q]

    def get_checkpoint(self):
        r"""
        :return: all data necessary to reconstruct the state. In this case member ``sites`` 
        :rtype: dict[tuple(int,int): torch.tensor]
        
        This function is called by optimizer to create checkpoints during 
        the optimization process.
        """
        return (self.sites, self.q)

    def load_checkpoint(self,checkpoint_file):
        r"""
        :param checkpoint_file: path to checkpoint file 
        :type checkpoint_file: str
        
        Initializes the state according to the supplied checkpoint file.

        .. note:: 

            The `vertexToSite` mapping function is not a part of checkpoint and must 
            be provided either when instantiating IPEPS_ABELIAN or afterwards.
        """
        checkpoint= torch.load(checkpoint_file,map_location=self.device)
        self.sites, self.q= checkpoint["parameters"]
        for site_t in self.sites.values(): site_t.requires_grad_(False)
        self.q.requires_grad_(False)
        if True in [s.is_complex() for s in self.sites.values()]:
            self.dtype= torch.complex128

    def write_to_file(self,outputfile,aux_seq=[0,1,2,3], tol=1.0e-14, normalize=False):
        """
        Writes state to file. See :meth:`write_ipeps`.
        """
        write_ipeps_1s_q(self,outputfile,aux_seq=aux_seq, tol=tol, normalize=normalize)

    def extend_bond_dim(self, new_d):
        r"""
        :param state: wavefunction to modify
        :param new_d: new enlarged auxiliary bond dimension
        :type state: IPEPS_1S_Q
        :type new_d: int
        :return: wavefunction with enlarged auxiliary bond dimensions
        :rtype: IPEPS_1S_Q

        Take IPEPS_1S_Q and enlarge all auxiliary bond dimensions of on-site tensor up to 
        size ``new_d``
        """
        new_state = self
        for coord,site in new_state.sites.items():
            dims = site.size()
            size_check = [new_d >= d for d in dims[1:]]
            if False in size_check:
                raise ValueError("Desired dimension is smaller than following aux dimensions: "+str(size_check))

            new_site = torch.zeros((dims[0],new_d,new_d,new_d,new_d), dtype=self.dtype, device=self.device)
            new_site[:,:dims[1],:dims[2],:dims[3],:dims[4]] = site
            new_state.sites[coord] = new_site
        return new_state

    def __str__(self):
        print(f"q=(q_x,q_y) {self.q}")
        print(f"lX x lY: {self.lX} x {self.lY}")
        for nid,coord,site in [(t[0], *t[1]) for t in enumerate(self.sites.items())]:
            print(f"a{nid} {coord}: {site.size()}")
        
        # show tiling of a square lattice
        coord_list = list(self.sites.keys())
        mx, my = 3*self.lX, 3*self.lY
        label_spacing = 1+int(math.log10(len(self.sites.keys())))
        for y in range(-my,my):
            if y == -my:
                print("y\\x ", end="")
                for x in range(-mx,mx):
                    print(str(x)+label_spacing*" "+" ", end="")
                print("")
            print(f"{y:+} ", end="")
            for x in range(-mx,mx):
                print(f"a{coord_list.index(self.vertexToSite((x,y)))} ", end="")
            print("")
        
        return ""

def read_ipeps_1s_q(jsonfile, vertexToSite=None, aux_seq=[0,1,2,3], peps_args=cfg.peps_args,\
    global_args=cfg.global_args):
    
    # read q-vector
    with open(jsonfile) as j:
        raw_state = json.load(j)
        q= torch.from_numpy(read_bare_json_tensor_np_legacy(raw_state["q"]))

    _state= read_ipeps(jsonfile, vertexToSite, aux_seq, peps_args=peps_args,\
        global_args=global_args)
    state= IPEPS_1S_Q(sites=_state.sites,q=q, peps_args=peps_args, global_args=global_args)

    return state

def write_ipeps_1s_q(state, outputfile, aux_seq=[0,1,2,3], tol=1.0e-14, normalize=False,\
    peps_args=cfg.peps_args, global_args=cfg.global_args):
    r"""
    :param state: wavefunction to write out in json format
    :param outputfile: target file
    :param aux_seq: array specifying order in which the auxiliary indices of on-site tensors 
                    will be stored in the `outputfile`
    :param tol: minimum magnitude of tensor elements which are written out
    :param normalize: if True, on-site tensors are normalized before writing
    :type state: IPEPS
    :type ouputfile: str or Path object
    :type aux_seq: list[int]
    :type tol: float
    :type normalize: bool

    Parameter ``aux_seq`` defines the order of auxiliary indices relative to the convention 
    fixed in tn-torch in which the tensor elements are written out::
    
         0
        1A3 <=> [up, left, down, right]: aux_seq=[0,1,2,3]
         2
        
        for alternative order, eg.
        
         1
        0A2 <=> [left, up, right, down]: aux_seq=[1,0,3,2] 
         3

    """
    json_state= _write_ipeps_json(state, aux_seq=aux_seq, tol=tol, normalize=normalize,\
        peps_args=peps_args, global_args=global_args)
    json_state["q"]= serialize_bare_tensor_legacy(state.q)

    with open(outputfile,'w') as f:
        json.dump(json_state, f, indent=4, separators=(',', ': '))
