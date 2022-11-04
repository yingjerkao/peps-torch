import torch
from config import _torch_version_check
from ctm.generic.env import ENV
from ctm.generic.ctm_components import c2x2_LU, c2x2_LD, c2x2_RU, c2x2_RD
from tn_interface import contract, einsum
from tn_interface import contiguous, view, permute
from tn_interface import conj
import logging

log = logging.getLogger(__name__)

def _cast_to_real(t, fail_on_check=False, warn_on_check=True, imag_eps=1.0e-8,\
    who="unknown", **kwargs):
    if t.is_complex():
        if abs(t.imag)/(abs(t.real)+1.0e-8) > imag_eps:
            if warn_on_check:
                log.warning(f"Unexpected imaginary part "+who+" "+str(t))
            if fail_on_check: 
                raise RuntimeError("Unexpected imaginary part "+who+" "+str(t))
        return t.real
    return t


def _sym_pos_def_matrix(rdm, sym_pos_def=False, verbosity=0, who="unknown", **kwargs):
    rdm_asym = 0.5 * (rdm - rdm.conj().t())
    rdm = 0.5 * (rdm + rdm.conj().t())
    if verbosity > 0:
        log.info(f"{who} norm(rdm_sym) {rdm.norm()} norm(rdm_asym) {rdm_asym.norm()}")
    if sym_pos_def:
        with torch.no_grad():
            if _torch_version_check("1.8.1"):
                D, U=  torch.linalg.eigh(rdm)
            else:
                D, U= torch.symeig(rdm, eigenvectors=True)
            if D.min() < 0:
                log.info(f"{who} max(diag(rdm)) {D.max()} min(diag(rdm)) {D.min()}")
                D = torch.clamp(D, min=0)
                rdm_posdef = U @ torch.diag(D) @ U.conj().t()
                rdm.copy_(rdm_posdef)
    norm= _cast_to_real(rdm.diagonal().sum(),who=who,**kwargs)
    rdm = rdm / norm
    return rdm


def _sym_pos_def_rdm(rdm, sym_pos_def=False, verbosity=0, who=None,  **kwargs):
    assert len(rdm.size()) % 2 == 0, "invalid rank of RDM"
    nsites = len(rdm.size()) // 2

    orig_shape = rdm.size()
    rdm = rdm.reshape(torch.prod(torch.as_tensor(rdm.size())[:nsites]), -1)

    rdm = _sym_pos_def_matrix(rdm, sym_pos_def=sym_pos_def, verbosity=verbosity, who=who)
    rdm = rdm.reshape(orig_shape)
    return rdm


def rdm1x1(coord, state, env, operator=None, sym_pos_def=False, force_cpu=False, verbosity=0):
    r"""
    :param coord: vertex (x,y) for which reduced density matrix is constructed
    :param state: underlying wavefunction
    :param env: environment corresponding to ``state``
    :param state: 1-site operator to contract with the two physical indices of the rdm
    :param verbosity: logging verbosity
    :type coord: tuple(int,int)
    :type state: IPEPS
    :type env: ENV
    :type operator: torch.tensor
    :type verbosity: int
    :return: 1-site reduced density matrix with indices :math:`s;s'`. If an operator was provided,
             returns the expectation value of this operator (not normalized by the norm of the wavefunction).
    :rtype: torch.tensor

    Computes 1-site reduced density matrix :math:`\rho_{1x1}` centered on vertex ``coord`` by
    contracting the following tensor network::

        C--T-----C
        |  |     |
        T--A^+A--T
        |  |     |
        C--T-----C

    If no operator was provided, the physical indices `s` and `s'` of on-site tensor :math:`A`
    at vertex ``coord`` and it's hermitian conjugate :math:`A^\dagger` are left uncontracted.
    Else, they are contracted with the operator.
    """
    who = "rdm1x1"
    # C(-1,-1)--1->0
    # 0
    # 0
    # T(-1,0)--2
    # 1
    if force_cpu:
        C = env.C[(coord, (-1, -1))].cpu()
        T = env.T[(coord, (-1, 0))].cpu()
    else:
        C = env.C[(coord, (-1, -1))]
        T = env.T[(coord, (-1, 0))]
    rdm = contract(C, T, ([0], [0]))
    if verbosity > 0:
        print("rdm=CT " + str(rdm.size()))

    # C(-1,-1)--0
    # |
    # T(-1,0)--2->1
    # 1
    # 0
    # C(-1,1)--1->2
    if force_cpu:
        C = env.C[(coord, (-1, 1))].cpu()
    else:
        C = env.C[(coord, (-1, 1))]
    rdm = contract(rdm, C, ([1], [0]))
    if verbosity > 0:
        print("rdm=CTC " + str(rdm.size()))

    # C(-1,-1)--0
    # |
    # T(-1,0)--1
    # |             0->2
    # C(-1,1)--2 1--T(0,1)--2->3
    if force_cpu:
        T = env.T[(coord, (0, 1))].cpu()
    else:
        T = env.T[(coord, (0, 1))]
    rdm = contract(rdm, T, ([2], [1]))
    if verbosity > 0:
        print("rdm=CTCT " + str(rdm.size()))

    # TODO - more efficent contraction with uncontracted-double-layer on-site tensor
    #       Possibly reshape indices 1,2 of rdm, which are to be contracted with
    #       on-site tensor and contract bra,ket in two steps instead of creating
    #       double layer tensor
    #    /
    # --A--
    #  /|s
    #
    # s'|/
    # --A--
    #  /
    #
    if force_cpu:
        a_1layer = state.site(coord).cpu()
    else:
        a_1layer = state.site(coord)
    dimsA = a_1layer.size()
    if operator == None:
        a = contiguous(einsum('mefgh,nabcd->eafbgchdmn', a_1layer, conj(a_1layer)))
        a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, dimsA[0], dimsA[0]))
    else:
        a = contiguous(einsum('mefgh,nm,nabcd->eafbgchd', a_1layer, operator, conj(a_1layer)))
        a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2))

    # C(-1,-1)--0
    # |
    # |             0->2
    # T(-1,0)--1 1--a--3
    # |             2[\45(s,s')]
    # |             2
    # C(-1,1)-------T(0,1)--3->1
    rdm = contract(rdm, a, ([1, 2], [1, 2]))
    if verbosity > 0:
        print("rdm=CTCTa " + str(rdm.size()))

    # C(-1,-1)--0 0--T(0,-1)--2->0
    # |              1
    # |              2
    # T(-1,0)--------a--3->2
    # |              |[\45->34(s,s')]
    # |              |
    # C(-1,1)--------T(0,1)--1
    if force_cpu:
        T = env.T[(coord, (0, -1))].cpu()
    else:
        T = env.T[(coord, (0, -1))]
    rdm = contract(T, rdm, ([0, 1], [0, 2]))
    if verbosity > 0:
        print("rdm=CTCTaT " + str(rdm.size()))

    # C(-1,-1)--T(0,-1)--0 0--C(1,-1)
    # |         |             1->0
    # |         |
    # T(-1,0)---a--2
    # |         |[\34(s,s')]
    # |         |
    # C(-1,1)---T(0,1)--0->1
    if force_cpu:
        C = env.C[(coord, (1, -1))].cpu()
    else:
        C = env.C[(coord, (1, -1))]
    rdm = contract(C, rdm, ([0], [0]))
    if verbosity > 0:
        print("rdm=CTCTaTC " + str(rdm.size()))

    # C(-1,-1)--T(0,-1)-----C(1,-1)
    # |         |               0
    # |         |               0
    # T(-1,0)---a--2 1------T(1,0)
    # |         |\34->23(s,s')  2->0
    # |         |
    # C(-1,1)---T(0,1)--1
    if force_cpu:
        T = env.T[(coord, (1, 0))].cpu()
    else:
        T = env.T[(coord, (1, 0))]
    rdm = contract(T, rdm, ([0, 1], [0, 2]))

    if verbosity > 0:
        print("rdm=CTCTaTCT " + str(rdm.size()))
    # C(-1,-1)--T(0,-1)--------C(1,-1)
    # |         |                |
    # |         |                |
    # T(-1,0)---a--------------T(1,0)
    # |         |\23->12(s,s')   0
    # |         |                0
    # C(-1,1)---T(0,1)--1 1----C(1,1)
    if force_cpu:
        C = env.C[(coord, (1, 1))].cpu()
    else:
        C = env.C[(coord, (1, 1))]
    rdm = contract(rdm, C, ([0, 1], [0, 1]))
    if verbosity > 0:
        print("rdm=CTCTaTCTC " + str(rdm.size()))

    # symmetrize and normalize
    if operator == None:
        rdm = _sym_pos_def_rdm(rdm, sym_pos_def=sym_pos_def, verbosity=verbosity, who=who)
    return rdm


def rdm2x1(coord, state, env, sym_pos_def=False, verbosity=0):
    r"""
    :param coord: vertex (x,y) specifies position of 2x1 subsystem
    :param state: underlying wavefunction
    :param env: environment corresponding to ``state``
    :param verbosity: logging verbosity
    :type coord: tuple(int,int)
    :type state: IPEPS
    :type env: ENV
    :type verbosity: int
    :return: 2-site reduced density matrix with indices :math:`s_0s_1;s'_0s'_1`
    :rtype: torch.tensor

    Computes 2-site reduced density matrix :math:`\rho_{2x1}` of a horizontal
    2x1 subsystem using following strategy:

        1. compute four individual corners
        2. construct right and left half of the network
        3. contract right and left halt to obtain final reduced density matrix

    ::

        C--T------------T------------------C = C2x2_LU(coord)--C2x2(coord+(1,0))
        |  |            |                  |   |               |
        T--A^+A(coord)--A^+A(coord+(1,0))--T   C2x1_LD(coord)--C2x1(coord+(1,0))
        |  |            |                  |
        C--T------------T------------------C

    The physical indices `s` and `s'` of on-sites tensors :math:`A` (and :math:`A^\dagger`)
    at vertices ``coord``, ``coord+(1,0)`` are left uncontracted
    """
    who = "rdm2x1"
    # ----- building C2x2_LU ----------------------------------------------------
    C = env.C[(state.vertexToSite(coord), (-1, -1))]
    T1 = env.T[(state.vertexToSite(coord), (0, -1))]
    T2 = env.T[(state.vertexToSite(coord), (-1, 0))]
    dimsA = state.site(coord).size()
    a = einsum('mefgh,nabcd->eafbgchdmn', state.site(coord), conj(state.site(coord)))
    a = view(contiguous(a), \
             (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, dimsA[0], dimsA[0]))

    # C--10--T1--2
    # 0      1
    C2x2_LU = contract(C, T1, ([1], [0]))

    # C------T1--2->1
    # 0   1->0
    # 0
    # T2--2->3
    # 1->2
    C2x2_LU = contract(C2x2_LU, T2, ([0], [0]))

    # C-------T1--1->0
    # |       0
    # |       0
    # T2--3 1 a--3
    # 2->1    2\45
    C2x2_LU = contract(C2x2_LU, a, ([0, 3], [0, 1]))

    # permute 012345->120345
    # reshape (12)(03)45->0123
    # C2x2--1
    # |\23
    # 0
    C2x2_LU = permute(C2x2_LU, (1, 2, 0, 3, 4, 5))
    C2x2_LU = view(contiguous(C2x2_LU), \
                   (T2.size(1) * a.size(2), T1.size(2) * a.size(3), dimsA[0], dimsA[0]))
    if verbosity > 0:
        print("C2X2 LU " + str(coord) + "->" + str(state.vertexToSite(coord)) + " (-1,-1): " + str(C2x2_LU.size()))

    # ----- building C2x1_LD ----------------------------------------------------
    C = env.C[(state.vertexToSite(coord), (-1, 1))]
    T2 = env.T[(state.vertexToSite(coord), (0, 1))]

    # 0    0->1
    # C--1 1--T2--2
    C2x1_LD = contract(C, T2, ([1], [1]))

    # reshape (01)2->(0)1
    # 0
    # |
    # C2x1--1
    C2x1_LD = view(contiguous(C2x1_LD), (C.size(0) * T2.size(0), T2.size(2)))
    if verbosity > 0:
        print("C2X1 LD " + str(coord) + "->" + str(state.vertexToSite(coord)) + " (-1,1): " + str(C2x1_LD.size()))

    # ----- build left part C2x2_LU--C2x1_LD ------------------------------------
    # C2x2_LU--1
    # |\23
    # 0
    # 0
    # C2x1_LD--1->0
    # TODO is it worthy(performance-wise) to instead overwrite one of C2x2_LU,C2x2_RU ?
    left_half = contract(C2x1_LD, C2x2_LU, ([0], [0]))

    # ----- building C2x2_RU ----------------------------------------------------
    vec = (1, 0)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C = env.C[(shift_coord, (1, -1))]
    T1 = env.T[(shift_coord, (1, 0))]
    T2 = env.T[(shift_coord, (0, -1))]
    dimsA = state.site(shift_coord).size()
    a = einsum('mefgh,nabcd->eafbgchdmn', state.site(shift_coord), conj(state.site(shift_coord)))
    a = view(contiguous(a), \
             (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, dimsA[0], dimsA[0]))

    # 0--C
    #    1
    #    0
    # 1--T1
    #    2
    C2x2_RU = contract(C, T1, ([1], [0]))

    # 2<-0--T2--2 0--C
    #    3<-1        |
    #          0<-1--T1
    #             1<-2
    C2x2_RU = contract(C2x2_RU, T2, ([0], [2]))

    # 1<-2--T2------C
    #       3       |
    #    45\0       |
    # 2<-1--a--3 0--T1
    #    3<-2    0<-1
    C2x2_RU = contract(C2x2_RU, a, ([0, 3], [3, 0]))

    # permute 012334->120345
    # reshape (12)(03)45->0123
    # 0--C2x2
    # 23/|
    #    1
    C2x2_RU = permute(C2x2_RU, (1, 2, 0, 3, 4, 5))
    C2x2_RU = view(contiguous(C2x2_RU), \
                   (T2.size(0) * a.size(1), T1.size(2) * a.size(2), dimsA[0], dimsA[0]))
    if verbosity > 0:
        print("C2X2 RU " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shift_coord) + " (1,-1): " + str(
            C2x2_RU.size()))

    # ----- building C2x1_RD ----------------------------------------------------
    C = env.C[(shift_coord, (1, 1))]
    T1 = env.T[(shift_coord, (0, 1))]

    #    1<-0        0
    # 2<-1--T1--2 1--C
    C2x1_RD = contract(C, T1, ([1], [2]))

    # reshape (01)2->(0)1
    C2x1_RD = view(contiguous(C2x1_RD), (C.size(0) * T1.size(0), T1.size(1)))

    #    0
    #    |
    # 1--C2x1
    if verbosity > 0:
        print("C2X1 RD " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shift_coord) + " (1,1): " + str(
            C2x1_RD.size()))

    # ----- build right part C2x2_RU--C2x1_RD -----------------------------------
    # 1<-0--C2x2_RU
    #       |\23
    #       1
    #       0
    # 0<-1--C2x1_RD
    right_half = contract(C2x1_RD, C2x2_RU, ([0], [1]))

    # construct reduced density matrix by contracting left and right halfs
    # C2x2_LU--1 1----C2x2_RU
    # |\23->01        |\23
    # |               |
    # C2x1_LD--0 0----C2x1_RD
    rdm = contract(left_half, right_half, ([0, 1], [0, 1]))

    # permute into order of s0,s1;s0',s1' where primed indices
    # represent "ket"
    # 0123->0213
    # symmetrize and normalize
    rdm = contiguous(permute(rdm, (0, 2, 1, 3)))
    rdm = _sym_pos_def_rdm(rdm, sym_pos_def=sym_pos_def, verbosity=verbosity, who=who)

    return rdm


def rdm1x2(coord, state, env, sym_pos_def=False, verbosity=0):
    r"""
    :param coord: vertex (x,y) specifies position of 1x2 subsystem
    :param state: underlying wavefunction
    :param env: environment corresponding to ``state``
    :param verbosity: logging verbosity
    :type coord: tuple(int,int)
    :type state: IPEPS
    :type env: ENV
    :type verbosity: int
    :return: 2-site reduced density matrix with indices :math:`s_0s_1;s'_0s'_1`
    :rtype: torch.tensor

    Computes 2-site reduced density matrix :math:`\rho_{1x2}` of a vertical
    1x2 subsystem using following strategy:

        1. compute four individual corners
        2. construct upper and lower half of the network
        3. contract upper and lower halt to obtain final reduced density matrix

    ::

        C--T------------------C = C2x2_LU(coord)--------C1x2(coord)
        |  |                  |   |                     |
        T--A^+A(coord)--------T   C2x2_LD(coord+(0,1))--C1x2(coord+0,1))
        |  |                  |
        T--A^+A(coord+(0,1))--T
        |  |                  |
        C--T------------------C

    The physical indices `s` and `s'` of on-sites tensors :math:`A` (and :math:`A^\dagger`)
    at vertices ``coord``, ``coord+(0,1)`` are left uncontracted
    """
    who = "rdm1x2"
    # ----- building C2x2_LU ----------------------------------------------------
    C = env.C[(state.vertexToSite(coord), (-1, -1))]
    T1 = env.T[(state.vertexToSite(coord), (0, -1))]
    T2 = env.T[(state.vertexToSite(coord), (-1, 0))]
    dimsA = state.site(coord).size()
    a = einsum('mefgh,nabcd->eafbgchdmn', state.site(coord), conj(state.site(coord)))
    a = view(contiguous(a), \
             (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, dimsA[0], dimsA[0]))

    # C--10--T1--2
    # 0   1
    C2x2_LU = contract(C, T1, ([1], [0]))

    # C------T1--2->1
    # 0      1->0
    # 0
    # T2--2->3
    # 1->2
    C2x2_LU = contract(C2x2_LU, T2, ([0], [0]))

    # C-------T1--1->0
    # |       0
    # |       0
    # T2--3 1 a--3
    # 2->1    2\45
    C2x2_LU = contract(C2x2_LU, a, ([0, 3], [0, 1]))

    # permute 012345->120345
    # reshape (12)(03)45->0123
    # C2x2--1
    # |\23
    # 0
    C2x2_LU = permute(C2x2_LU, (1, 2, 0, 3, 4, 5))
    C2x2_LU = view(contiguous(C2x2_LU), \
                   (T2.size(1) * a.size(2), T1.size(2) * a.size(3), dimsA[0], dimsA[0]))
    if verbosity > 0:
        print("C2X2 LU " + str(coord) + "->" + str(state.vertexToSite(coord)) + " (-1,-1): " + str(C2x2_LU.size()))

    # ----- building C1x2_RU ----------------------------------------------------
    C = env.C[(state.vertexToSite(coord), (1, -1))]
    T1 = env.T[(state.vertexToSite(coord), (1, 0))]

    # 0--C
    #    1
    #    0
    # 1--T1
    #    2
    C1x2_RU = contract(C, T1, ([1], [0]))

    # reshape (01)2->(0)1
    # 0--C1x2
    # 23/|
    #    1
    C1x2_RU = view(contiguous(C1x2_RU), (C.size(0) * T1.size(1), T1.size(2)))
    if verbosity > 0:
        print("C1X2 RU " + str(coord) + "->" + str(state.vertexToSite(coord)) + " (1,-1): " + str(C1x2_RU.size()))

    # ----- build upper part C2x2_LU--C1x2_RU -----------------------------------
    # C2x2_LU--1 0--C1x2_RU
    # |\23          |
    # 0->1          1->0
    upper_half = contract(C1x2_RU, C2x2_LU, ([0], [1]))

    # ----- building C2x2_LD ----------------------------------------------------
    vec = (0, 1)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C = env.C[(shift_coord, (-1, 1))]
    T1 = env.T[(shift_coord, (-1, 0))]
    T2 = env.T[(shift_coord, (0, 1))]
    dimsA = state.site(shift_coord).size()
    a = einsum('mefgh,nabcd->eafbgchdmn', state.site(shift_coord), conj(state.site(shift_coord)))
    a = view(contiguous(a), \
             (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, dimsA[0], dimsA[0]))

    # 0->1
    # T1--2
    # 1
    # 0
    # C--1->0
    C2x2_LD = contract(C, T1, ([0], [1]))

    # 1->0
    # T1--2->1
    # |
    # |       0->2
    # C--0 1--T2--2->3
    C2x2_LD = contract(C2x2_LD, T2, ([0], [1]))

    # 0        0->2
    # T1--1 1--a--3
    # |        2\45
    # |        2
    # C--------T2--3->1
    C2x2_LD = contract(C2x2_LD, a, ([1, 2], [1, 2]))

    # permute 012345->021345
    # reshape (02)(13)45->0123
    # 0
    # |/23
    # C2x2--1
    C2x2_LD = permute(C2x2_LD, (0, 2, 1, 3, 4, 5))
    C2x2_LD = view(contiguous(C2x2_LD), \
                   (T1.size(0) * a.size(0), T2.size(2) * a.size(3), dimsA[0], dimsA[0]))
    if verbosity > 0:
        print("C2X2 LD " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shift_coord) + " (-1,1): " + str(
            C2x2_LD.size()))

    # ----- building C2x2_RD ----------------------------------------------------
    C = env.C[(shift_coord, (1, 1))]
    T2 = env.T[(shift_coord, (1, 0))]

    #       0
    #    1--T2
    #       2
    #       0
    # 2<-1--C
    C1x2_RD = contract(T2, C, ([2], [0]))

    # permute 012->021
    # reshape 0(12)->0(1)
    C1x2_RD = view(contiguous(permute(C1x2_RD, (0, 2, 1))), \
                   (T2.size()[0], C.size()[1] * T2.size()[1]))

    #    0
    #    |
    # 1--C1x2
    if verbosity > 0:
        print("C1X2 RD " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shift_coord) + " (1,1): " + str(
            C1x2_RD.size()))

    # ----- build lower part C2x2_LD--C1x2_RD -----------------------------------
    # 0->1          0
    # |/23          |
    # C2x2_LD--1 1--C1x2_RD
    lower_half = contract(C1x2_RD, C2x2_LD, ([1], [1]))

    # construct reduced density matrix by contracting lower and upper halfs
    # C2x2_LU------C1x2_RU
    # |\23->01     |
    # 1            0
    # 1            0
    # |/23         |
    # C2x2_LD------C1x2_RD
    rdm = contract(upper_half, lower_half, ([0, 1], [0, 1]))

    # permute into order of s0,s1;s0',s1' where primed indices
    # represent "ket"
    # 0123->0213
    # symmetrize and normalize
    rdm = contiguous(permute(rdm, (0, 2, 1, 3)))
    rdm = _sym_pos_def_rdm(rdm, sym_pos_def=sym_pos_def, verbosity=verbosity, who=who)

    return rdm


def rdm2x2_NNN_11(coord, state, env, sym_pos_def=False, verbosity=0):
    r"""
    :param coord: vertex (x,y) specifies upper left site of 2x2 subsystem
    :param state: underlying wavefunction
    :param env: environment corresponding to ``state``
    :param verbosity: logging verbosity
    :type coord: tuple(int,int)
    :type state: IPEPS
    :type env: ENV
    :type verbosity: int
    :return: 2-site reduced density matrix with indices :math:`s_0s_1;s'_0s'_1
    :rtype: torch.tensor

    Computes 2-site reduced density matrix :math:`\rho_{NNN,11}` of two-site subsystem 
    across (1,1) diagonal specified by the vertex ``coord`` of its upper left corner using strategy:

        1. compute four individual corners
        2. construct upper and lower half of the network
        3. contract upper and lower half to obtain final reduced density matrix

    ::

        C--T------------------T------------------C = C2x2_LU(coord)--------C2x2(coord+(1,0))
        |  |                  |                  |   |                     |
        T--A^+A(coord)--------A^+A(coord+(1,0))--T   C2x2_LD(coord+(0,1))--C2x2(coord+(1,1))
        |  |                  |                  | 
        T--A^+A(coord+(0,1))--A^+A(coord+(1,1))--T
        |  |                  |                  |
        C--T------------------T------------------C

    The physical indices `s` and `s'` of on-sites tensors :math:`A` (and :math:`A^\dagger`)
    at vertices ``coord`` and ``coord+(1,1)`` are left uncontracted and given in the same order::

        s0 x
        x  s1

    """
    who = "rdm2x2_NNN_11"
    # ----- building C2x2_LU ----------------------------------------------------
    C2X2_LU= c2x2_LU(coord,state,env,mode='sl-open',verbosity=verbosity)

    # ----- building C2X2_RU ----------------------------------------------------
    vec = (1, 0)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C2X2_RU= c2x2_RU(shift_coord, state, env, mode='sl',verbosity=verbosity) 

    # ----- build upper part C2x2_LU--C2X2_RU -----------------------------------
    # C2x2_LU--1 0--C2X2_RU           C2x2_LU------C2X2_RU
    # |\23->12      |       & permute |\12->23     |
    # 0             1->3              0            1
    # TODO is it worthy(performance-wise) to instead overwrite one of C2x2_LU,C2X2_RU ?
    upper_half = contract(C2X2_LU, C2X2_RU, ([1], [0]))
    upper_half = permute(upper_half, (0, 3, 1, 2))

    # ----- building C2X2_RD ----------------------------------------------------
    vec = (1, 1)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C2X2_RD= c2x2_RD(shift_coord,state,env,mode='sl-open',verbosity=verbosity)

    # ----- building C2x2_LD ----------------------------------------------------
    vec = (0, 1)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C2X2_LD= c2x2_LD(shift_coord, state, env, mode='sl', verbosity=verbosity)

    # ----- build lower part C2X2_LD--C2X2_RD -----------------------------------
    # 0             0->1
    # |             |/23
    # C2X2_LD--1 1--C2x2_RD
    # TODO is it worthy(performance-wise) to instead overwrite one of C2X2_LD,C2x2_RD ?
    lower_half = contract(C2X2_LD, C2X2_RD, ([1], [1]))

    # construct reduced density matrix by contracting lower and upper halfs
    # C2x2_LU------C2x2_RU
    # |\23->01     |
    # 0            1
    # 0            1
    # |            |/23
    # C2X2_LD------C2x2_RD
    rdm = contract(upper_half, lower_half, ([0, 1], [0, 1]))

    # permute into order of s0,s1;s0',s1' where primed indices
    # represent "ket"
    # 0123->0213
    # symmetrize and normalize
    rdm = contiguous(permute(rdm, (0, 2, 1, 3)))
    rdm = _sym_pos_def_rdm(rdm, sym_pos_def=sym_pos_def, verbosity=verbosity, who=who)

    return rdm


def rdm2x2_NNN_1n1(coord, state, env, sym_pos_def=False, verbosity=0):
    r"""
    :param coord: vertex (x,y) specifies upper left site of 2x2 subsystem
    :param state: underlying wavefunction
    :param env: environment corresponding to ``state``
    :param verbosity: logging verbosity
    :type coord: tuple(int,int)
    :type state: IPEPS
    :type env: ENV
    :type verbosity: int
    :return: 2-site reduced density matrix with indices :math:`s_0s_1;s'_0s'_1`
    :rtype: torch.tensor

    Computes 2-site reduced density matrix :math:`\rho_{NNN,1n1}` of two-site subsystem 
    across (1,-1) diagonal specified by the vertex ``coord`` of its lower left corner using strategy:

        1. compute four individual corners
        2. construct upper and lower half of the network
        3. contract upper and lower half to obtain final reduced density matrix

    ::

        C--T------------------T------------------C = C2x2_LU(coord+(0,-1))-C2x2(coord+(1,-1))
        |  |                  |                  |   |                     |
        T--A^+A(coord+(0,-1))-A^+A(coord+(1,-1))-T   C2x2_LD(coord)--------C2x2(coord+(1,0))
        |  |                  |                  | 
        T--A^+A(coord)--------A^+A(coord+(1,0))--T
        |  |                  |                  |
        C--T------------------T------------------C

    The physical indices `s` and `s'` of on-sites tensors :math:`A` (and :math:`A^\dagger`)
    at vertices ``coord`` and ``coord+(1,-1)`` are left uncontracted and given in the same order::

        x  s1
        s0 x

    """
    who = "rdm2x2_NNN_1n1"
    # ----- building C2X2_LU ----------------------------------------------------
    vec = (0, -1)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C2X2_LU= c2x2_LU(shift_coord,state,env,mode='sl',verbosity=verbosity)

    # ----- building C2x2_RU ----------------------------------------------------
    vec = (1, -1)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C2X2_RU= c2x2_RU(shift_coord, state, env, mode='sl-open', verbosity=verbosity)

    # ----- build upper part C2x2_LU--C2X2_RU -----------------------------------
    # C2x2_LU--1 0--C2X2_RU
    # |             |\23
    # 0             1
    # TODO is it worthy(performance-wise) to instead overwrite one of C2x2_LU,C2X2_RU ?
    upper_half = contract(C2X2_LU, C2X2_RU, ([1], [0]))

    # ----- building C2X2_RD ----------------------------------------------------
    vec = (1, 0)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C2X2_RD= c2x2_RD(shift_coord,state,env,mode='sl',verbosity=verbosity)    

    # ----- building C2X2_LD ----------------------------------------------------
    C2X2_LD= c2x2_LD(coord,state,env,mode='sl-open',verbosity=verbosity)

    # ----- build lower part C2X2_LD--C2X2_RD -----------------------------------
    # 0             0->3                 0            3->1
    # |/23->12      |          & permute |/12->23     |
    # C2X2_LD--1 1--C2X2_RD              C2X2_LD------C2X2_RD
    # TODO is it worthy(performance-wise) to instead overwrite one of C2X2_LD,C2X2_RD ?
    lower_half = contract(C2X2_LD, C2X2_RD, ([1], [1]))
    lower_half = permute(lower_half, (0, 3, 1, 2))

    # construct reduced density matrix by contracting lower and upper halfs
    # C2X2_LU------C2X2_RU
    # |            |\23
    # 0            1
    # 0            1
    # |/23->01     |
    # C2X2_LD------C2X2_RD
    rdm = contract(lower_half, upper_half, ([0, 1], [0, 1]))

    # permute into order of s0,s1;s0',s1' where primed indices
    # represent "ket"
    # 0123->0213
    # symmetrize and normalize
    rdm = contiguous(permute(rdm, (0, 2, 1, 3)))
    rdm = _sym_pos_def_rdm(rdm, sym_pos_def=sym_pos_def, verbosity=verbosity, who=who)

    return rdm


def rdm2x2(coord, state, env, sym_pos_def=False, verbosity=0):
    r"""
    :param coord: vertex (x,y) specifies upper left site of 2x2 subsystem
    :param state: underlying wavefunction
    :param env: environment corresponding to ``state``
    :param verbosity: logging verbosity
    :type coord: tuple(int,int)
    :type state: IPEPS
    :type env: ENV
    :type verbosity: int
    :return: 4-site reduced density matrix with indices :math:`s_0s_1s_2s_3;s'_0s'_1s'_2s'_3`
    :rtype: torch.tensor

    Computes 4-site reduced density matrix :math:`\rho_{2x2}` of 2x2 subsystem specified
    by the vertex ``coord`` of its upper left corner using strategy:

        1. compute four individual corners
        2. construct upper and lower half of the network
        3. contract upper and lower half to obtain final reduced density matrix

    ::

        C--T------------------T------------------C = C2x2_LU(coord)--------C2x2(coord+(1,0))
        |  |                  |                  |   |                     |
        T--A^+A(coord)--------A^+A(coord+(1,0))--T   C2x2_LD(coord+(0,1))--C2x2(coord+(1,1))
        |  |                  |                  | 
        T--A^+A(coord+(0,1))--A^+A(coord+(1,1))--T
        |  |                  |                  |
        C--T------------------T------------------C

    The physical indices `s` and `s'` of on-sites tensors :math:`A` (and :math:`A^\dagger`)
    at vertices ``coord``, ``coord+(1,0)``, ``coord+(0,1)``, and ``coord+(1,1)`` are
    left uncontracted and given in the same order::

        s0 s1
        s2 s3

    """
    who = "rdm2x2"
    # ----- building C2x2_LU ----------------------------------------------------
    C = env.C[(state.vertexToSite(coord), (-1, -1))]
    T1 = env.T[(state.vertexToSite(coord), (0, -1))]
    T2 = env.T[(state.vertexToSite(coord), (-1, 0))]
    dimsA = state.site(coord).size()
    a = contiguous(einsum('mefgh,nabcd->eafbgchdmn', state.site(coord), conj(state.site(coord))))
    a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, dimsA[0], dimsA[0]))

    # C--10--T1--2
    # 0   1
    C2x2_LU = contract(C, T1, ([1], [0]))

    # C------T1--2->1
    # 0      1->0
    # 0
    # T2--2->3
    # 1->2
    C2x2_LU = contract(C2x2_LU, T2, ([0], [0]))

    # C-------T1--1->0
    # |       0
    # |       0
    # T2--3 1 a--3
    # 2->1    2\45
    C2x2_LU = contract(C2x2_LU, a, ([0, 3], [0, 1]))

    # permute 012345->120345
    # reshape (12)(03)45->0123
    # C2x2--1
    # |\23
    # 0
    C2x2_LU = contiguous(permute(C2x2_LU, (1, 2, 0, 3, 4, 5)))
    C2x2_LU = view(C2x2_LU, (T2.size(1) * a.size(2), T1.size(2) * a.size(3), dimsA[0], dimsA[0]))
    if verbosity > 0:
        print("C2X2 LU " + str(coord) + "->" + str(state.vertexToSite(coord)) + " (-1,-1): " + str(C2x2_LU.size()))

    # ----- building C2x2_RU ----------------------------------------------------
    vec = (1, 0)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C = env.C[(shift_coord, (1, -1))]
    T1 = env.T[(shift_coord, (1, 0))]
    T2 = env.T[(shift_coord, (0, -1))]
    dimsA = state.site(shift_coord).size()
    a = contiguous(einsum('mefgh,nabcd->eafbgchdmn', state.site(shift_coord), conj(state.site(shift_coord))))
    a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, dimsA[0], dimsA[0]))

    # 0--C
    #    1
    #    0
    # 1--T1
    #     2
    C2x2_RU = contract(C, T1, ([1], [0]))

    # 2<-0--T2--2 0--C
    #    3<-1        |
    #          0<-1--T1
    #             1<-2
    C2x2_RU = contract(C2x2_RU, T2, ([0], [2]))

    # 1<-2--T2------C
    #       3       |
    #    45\0       |
    # 2<-1--a--3 0--T1
    #    3<-2    0<-1
    C2x2_RU = contract(C2x2_RU, a, ([0, 3], [3, 0]))

    # permute 012334->120345
    # reshape (12)(03)45->0123
    # 0--C2x2
    # 23/|
    #    1
    C2x2_RU = contiguous(permute(C2x2_RU, (1, 2, 0, 3, 4, 5)))
    C2x2_RU = view(C2x2_RU, (T2.size(0) * a.size(1), T1.size(2) * a.size(2), dimsA[0], dimsA[0]))
    if verbosity > 0:
        print("C2X2 RU " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shift_coord) + " (1,-1): " + str(
            C2x2_RU.size()))

    # ----- build upper part C2x2_LU--C2x2_RU -----------------------------------
    # C2x2_LU--1 0--C2x2_RU           C2x2_LU------C2x2_RU
    # |\23->12   |\23->45   & permute |\12->23     |\45
    # 0          1->3                 0            3->1
    # TODO is it worthy(performance-wise) to instead overwrite one of C2x2_LU,C2x2_RU ?
    upper_half = contract(C2x2_LU, C2x2_RU, ([1], [0]))
    upper_half = permute(upper_half, (0, 3, 1, 2, 4, 5))

    # ----- building C2x2_RD ----------------------------------------------------
    vec = (1, 1)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C = env.C[(shift_coord, (1, 1))]
    T1 = env.T[(shift_coord, (0, 1))]
    T2 = env.T[(shift_coord, (1, 0))]
    dimsA = state.site(shift_coord).size()
    a = contiguous(einsum('mefgh,nabcd->eafbgchdmn', state.site(shift_coord), conj(state.site(shift_coord))))
    a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, dimsA[0], dimsA[0]))

    #   1<-0        0
    # 2<-1--T1--2 1--C
    C2x2_RD = contract(C, T1, ([1], [2]))

    #         2<-0
    #      3<-1--T2
    #            2
    #    0<-1    0
    # 1<-2--T1---C
    C2x2_RD = contract(C2x2_RD, T2, ([0], [2]))

    #    2<-0    1<-2
    # 3<-1--a--3 3--T2
    #       2\45    |
    #       0       |
    # 0<-1--T1------C
    C2x2_RD = contract(C2x2_RD, a, ([0, 3], [2, 3]))

    # permute 012345->120345
    # reshape (12)(03)45->0123
    C2x2_RD = contiguous(permute(C2x2_RD, (1, 2, 0, 3, 4, 5)))
    C2x2_RD = view(C2x2_RD, (T2.size(0) * a.size(0), T1.size(1) * a.size(1), dimsA[0], dimsA[0]))

    #    0
    #    |/23
    # 1--C2x2
    if verbosity > 0:
        print("C2X2 RD " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shift_coord) + " (1,1): " + str(
            C2x2_RD.size()))

    # ----- building C2x2_LD ----------------------------------------------------
    vec = (0, 1)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    C = env.C[(shift_coord, (-1, 1))]
    T1 = env.T[(shift_coord, (-1, 0))]
    T2 = env.T[(shift_coord, (0, 1))]
    dimsA = state.site(shift_coord).size()
    a = contiguous(einsum('mefgh,nabcd->eafbgchdmn', state.site(shift_coord), conj(state.site(shift_coord))))
    a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, dimsA[0], dimsA[0]))

    # 0->1
    # T1--2
    # 1
    # 0
    # C--1->0
    C2x2_LD = contract(C, T1, ([0], [1]))

    # 1->0
    # T1--2->1
    # |
    # |       0->2
    # C--0 1--T2--2->3
    C2x2_LD = contract(C2x2_LD, T2, ([0], [1]))

    # 0        0->2
    # T1--1 1--a--3
    # |        2\45
    # |        2
    # C--------T2--3->1
    C2x2_LD = contract(C2x2_LD, a, ([1, 2], [1, 2]))

    # permute 012345->021345
    # reshape (02)(13)45->0123
    # 0
    # |/23
    # C2x2--1
    C2x2_LD = contiguous(permute(C2x2_LD, (0, 2, 1, 3, 4, 5)))
    C2x2_LD = view(C2x2_LD, (T1.size(0) * a.size(0), T2.size(2) * a.size(3), dimsA[0], dimsA[0]))
    if verbosity > 0:
        print("C2X2 LD " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shift_coord) + " (-1,1): " + str(
            C2x2_LD.size()))

    # ----- build lower part C2x2_LD--C2x2_RD -----------------------------------
    # 0             0->3                 0            3->1
    # |/23->12      |/23->45   & permute |/12->23     |/45
    # C2x2_LD--1 1--C2x2_RD              C2x2_LD------C2x2_RD
    # TODO is it worthy(performance-wise) to instead overwrite one of C2x2_LD,C2x2_RD ?
    lower_half = contract(C2x2_LD, C2x2_RD, ([1], [1]))
    lower_half = permute(lower_half, (0, 3, 1, 2, 4, 5))

    # construct reduced density matrix by contracting lower and upper halfs
    # C2x2_LU------C2x2_RU
    # |\23->01     |\45->23
    # 0            1
    # 0            1
    # |/23->45     |/45->67
    # C2x2_LD------C2x2_RD
    rdm = contract(upper_half, lower_half, ([0, 1], [0, 1]))

    # permute into order of s0,s1,s2,s3;s0',s1',s2',s3' where primed indices
    # represent "ket"
    # 01234567->02461357
    # symmetrize and normalize
    rdm = contiguous(permute(rdm, (0, 2, 4, 6, 1, 3, 5, 7)))
    rdm = _sym_pos_def_rdm(rdm, sym_pos_def=sym_pos_def, verbosity=verbosity, who=who)

    return rdm


def _CTCT_LD(coord,state,env):
    C1, C2, C3, C4, T1, T2, T3, T4= env.get_site_env_t(coord,state)
    #  C1--1->0
    #  0
    #  0
    #  T4--2
    #  1
    CTC_LD = torch.tensordot(C1,T4,([0],[0]))
    #  C1--0
    #  |
    #  T4--2->1
    #  1
    #  0
    #  C4--1->2
    CTC_LD = torch.tensordot(CTC_LD,C4,([1],[0]))
    # C1--0
    # |
    # T4--1
    # |        0->2
    # C4--2 1--T3--2->3
    # 
    CTC_LD = torch.tensordot(CTC_LD,T3,([2],[1]))
    return CTC_LD

def _Lhalf_1x2(coord,state,env):
    Lhalf= _CTCT_LD(coord,state,env)
    # C1--0 0--T1[coord,(0,-1)]--2->1->0
    # |        1->2->1
    # T4--1->2
    # |     2->3
    # C4----T3--3->4
    T1= env.T[coord,(0,-1)]
    Lhalf= torch.tensordot(T1.permute(0,2,1).contiguous(),Lhalf,([0],[0]))
    return Lhalf

def _CTCT_RU(coord,state,env):
    C1, C2, C3, C4, T1, T2, T3, T4= env.get_site_env_t(coord,state)
    #       0
    #    1--T2
    #       2
    #       0
    # 2<-1--C3
    CTC_RU= torch.tensordot(T2,C3,([2],[0]))
    #    0--C2
    #       1
    #       0
    #    1--T2
    #       |
    #    2--C3
    CTC_RU= torch.tensordot(C2,CTC_RU,([1],[0]))
    #  0--T1--2 0--C2
    #     1        |
    #        2<-1--T2
    #              |
    #        3<-2--C3
    CTC_RU= torch.tensordot(T1,CTC_RU,([2],[0]))
    return CTC_RU

def _Rhalf_1x2(coord,state,env):
    Rhalf= _CTCT_RU(coord,state,env)
    #                     0--T1----C2
    #                        1     |
    #                           2--T2
    #        0->3                  |
    #  4<-1--T3[coord,(0,1)]--2 3--C3
    T3= env.T[coord,(0,1)]
    Rhalf= torch.tensordot(Rhalf,T3,([3],[2]))
    return Rhalf

def aux_rdm1x1(coord, state, env, sym_pos_def=False, verbosity=0):
    r"""
    :param coord: vertex (x,y) specifies upper left site of 2x2 subsystem
    :param state: underlying wavefunction
    :param env: environment corresponding to ``state``
    :param verbosity: logging verbosity
    :type coord: tuple(int,int)
    :type state: IPEPS
    :type env: ENV
    :type verbosity: int
    :return: 1-site auxilliary reduced density matrix
    :rtype: torch.tensor
    
    Builds 1x1 reduced density matrix by 
    contracting the following tensor network::

        C1--T1--C2
        |   |   |
        T4--  --T2
        |   |   |
        C4--T3--C3

    """
    who= "aux_rdm1x1"
    CTC_LD= _CTCT_LD(coord,state,env)
    CTC_RU= _CTCT_RU(coord,state,env)

    #   C1--0  0--T1-------C2
    #   |         1->2     |
    #   T4--1->0     3<-2--T2 
    #   |      1<-2        |
    #   C4--------T3--3 3--C3
    #
    rdm= torch.tensordot(CTC_LD,CTC_RU,([0,3],[0,3]))
    rdm= rdm.permute(2,0,1,3).contiguous()

    # 4i) unfuse the D^2 indices and permute to bra,ket
    #
    #   C----T----C      C------T--------C
    #   |    0    |      |      0,1      |
    #   T--1   3--T  =>  T--2,3     6,7--T
    #   |    2    |      |      4,5      | 
    #   C----T----C      C------T--------C
    #        
    a= state.site(coord)
    rdm= rdm.view([a.size(1)]*2+[a.size(2)]*2+[a.size(3)]*2+[a.size(4)]*2)
    rdm= rdm.permute(0,2,4,6,1,3,5,7).contiguous()

    return rdm

def aux_rdm1x2(coord, state, env, sym_pos_def=False, verbosity=0):
    r"""
    :param coord: vertex (x,y) specifies upper left site of 2x2 subsystem
    :param state: underlying wavefunction
    :param env: environment corresponding to ``state``
    :param verbosity: logging verbosity
    :type coord: tuple(int,int)
    :type state: IPEPS
    :type env: ENV
    :type verbosity: int
    :return: 1-site auxilliary reduced density matrix
    :rtype: torch.tensor
    
    Builds 1x1 reduced density matrix by 
    contracting the following tensor network::

        C1--T1[(0,0),(0,-1)]--T1[(1,0),(0,-1)]--C2
        |   |                 |                 |
        T4--                                  --T2
        |   |                 |                 |
        C4--T3[(0,0),(0,1)]---T3[(1,0),(0,1)]---C3

    """
    who= "aux_rdm1x1"
    Lhalf= _Lhalf_1x2(coord,state,env)
    Rhalf= _Rhalf_1x2(coord,state,env)

    #   C1----T1--0 0--T1----C2
    #   |     1->0     1->3  |
    #   T4--2->1       4<-2--T2
    #   |     3->2     3->5  |
    #   C4----T3--4 4--T3----C3
    #
    rdm= torch.tensordot(Lhalf,Rhalf,([0,4],[0,4]))
    # take anti-clockwise order
    rdm= rdm.permute(0,1,2,5,4,3).contiguous()

    # 4i) unfuse the D^2 indices and permute to bra,ket
    #
    #   C1----T1-------T1----C2
    #   |     0,1      10,11 |
    #   T4--2,3         8,9--T2
    #   |     4,5      6,7   |
    #   C4----T3-------T3----C3
    #        
    dims00= state.site(coord).size()
    dims10= state.site((coord[0]+1,coord[1])).size()
    rdm= rdm.view([dims00[1]]*2+[dims00[2]]*2+[dims00[3]]*2\
        +[dims10[3]]*2+[dims10[4]]*2+[dims10[1]]*2)
    rdm= rdm.permute(0,2,4,6,8,10, 1,3,5,7,9,11).contiguous()

    return rdm