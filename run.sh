export MKL_NUM_THREADS=1

# python main.py --chi 16 --bondim 2 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10 --device cuda:0 

# python main.py --chi 32 --bondim 2 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10 --device cuda:0 

# python main.py --chi 64 --bondim 2 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10 --device cuda:0 

# python main.py --chi 128 --bondim 2 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10 --device cuda:0 

# python main.py --chi 256 --bondim 2 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10 --device cuda:0 

python main.py --chi 16 --bondim 4 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 2 --device cuda:0 --GLOBALARGS_dtype complex128

# python main.py --chi 32 --bondim 4 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10 --device cuda:0

# python main.py --chi 64 --bondim 4 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10 --device cuda:0

# python main.py --chi 128 --bondim 4 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10 --device cuda:0

# python main.py --chi 16 --bondim 5 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10

# python main.py --chi 32 --bondim 5 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10

# python main.py --chi 64 --bondim 5 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10

# python main.py --chi 128 --bondim 2 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10

# export MKL_NUM_THREADS=8
# export OMP_NUM_THREADS=8
# python test.py
# export CYTNX_INC=$(python -c "exec(\"import cytnx\nprint(cytnx.__cpp_include__)\")")
# export CYTNX_LIB=$(python -c "exec(\"import cytnx\nprint(cytnx.__cpp_lib__)\")")/libcytnx.a
# export CYTNX_LINK="$(python -c "exec(\"import cytnx\nprint(cytnx.__cpp_linkflags__)\")")"
# export CYTNX_CXXFLAGS="$(python -c "exec(\"import cytnx\nprint(cytnx.__cpp_flags__)\")")"

# g++ -I${CYTNX_INC} ${CYTNX_CXXFLAGS} test.cpp ${CYTNX_LIB} ${CYTNX_LINK} -o test

# python main.py --chi 128 --bondim 4 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10
# python main.py --chi 128 --bondim 5 --CTMARGS_ctm_conv_tol 0 --CTMARGS_ctm_max_iter 10