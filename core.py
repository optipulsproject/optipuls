from dolfin import *
from mshr import *
import ufl
import numpy as np
from matplotlib import pyplot as plt
from numpy.polynomial import Polynomial
import json

# from tqdm import trange

import splines as spl

set_log_level(40)
parameters["form_compiler"]["quadrature_degree"] = 1

# Space and time discretization parameters
R = 0.0025
R_laser = 0.0002
Z = 0.0005
T, Nt = 0.010, 30
dt = T/Nt

# Model constants
theta_amb = Constant("295")
enthalpy = Constant("397000")
P_YAG = 1600.
absorb = 0.135
laser_pd = (absorb * P_YAG) / (pi * R_laser**2)
implicitness = Constant("1.0")

# Optimization parameters
alpha = 0. # temporarily exclude the control cost
beta = 1.
beta_w = 1.
gamma = 10**-5
iter_max = 5
tolerance = 10**-18
velocity_max = 0.12
target_point = Point(0, .5*Z)
threshold_temp = 1102.
pow_ = 6.

control_ref = np.zeros(Nt)

# Aggregate state
liquidus = 923.0
solidus = 858.0

class Domain_2(SubDomain):
    def inside(self, x, on_boundary):
        return x[0] < 0.6 * R

class Domain_3(SubDomain):
    def inside(self, x, on_boundary):
        return x[0] < 0.4 * R

class Domain_4(SubDomain):
    def inside(self, x, on_boundary):
        return x[0] < 0.2 * R

class Domain_5(SubDomain):
    def inside(self, x, on_boundary):
        return x[0] < 0.2 * R and x[1] > 0.5 * Z

class LaserBoundary(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary and x[1] > Z-DOLFIN_EPS and x[0] < R_laser
    
class EmptyBoundary(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary and \
            ((x[1] > Z-DOLFIN_EPS and x[0] >= R_laser) or x[1] < DOLFIN_EPS)

class SymAxisBoundary(SubDomain):
    def inside(self, x, on_boundary):
        return on_boundary and (x[0] < DOLFIN_EPS)
    
# Create and refine mesh
mesh = RectangleMesh(Point(0,0), Point(R,Z), 25, 5)

domain_2 = Domain_2()
domain_3 = Domain_3()
domain_4 = Domain_4()
domain_5 = Domain_5()
# near_laser = NearLaser()

edge_markers = MeshFunction("bool", mesh, mesh.topology().dim()-1)
domain_2.mark(edge_markers, True)
mesh = refine(mesh, edge_markers)

edge_markers = MeshFunction("bool", mesh, mesh.topology().dim()-1)
domain_3.mark(edge_markers, True)
mesh = refine(mesh, edge_markers)

edge_markers = MeshFunction("bool", mesh, mesh.topology().dim()-1)
domain_4.mark(edge_markers, True)
mesh = refine(mesh, edge_markers)

edge_markers = MeshFunction("bool", mesh, mesh.topology().dim()-1)
domain_5.mark(edge_markers, True)
mesh = refine(mesh, edge_markers)

x = SpatialCoordinate(mesh)

# Define function space 
V = FunctionSpace(mesh, "CG", 1)
V1 = FunctionSpace(mesh, "DG", 0)

boundary_markers = MeshFunction('size_t', mesh, mesh.topology().dim()-1)

laser_boundary = LaserBoundary()
laser_boundary.mark(boundary_markers, 1)

empty_boundary = EmptyBoundary()
empty_boundary.mark(boundary_markers, 2)

sym_axis_boundary = SymAxisBoundary()
sym_axis_boundary.mark(boundary_markers, 3)

ds = Measure('ds', domain=mesh, subdomain_data=boundary_markers)


with open('material.json') as file:
    material = json.load(file)

spline = spl.gen_hermite_spline(
    material['heat capacity']['knots'],
    material['heat capacity']['values'])
c = spl.spline_as_ufl(spline, material['heat capacity']['knots'])

spline = spl.gen_hermite_spline(
    material['density']['knots'],
    material['density']['values'],
    extrapolation='linear')
rho = spl.spline_as_ufl(spline, material['density']['knots'])

spline = spl.gen_hermite_spline(
    material['thermal conductivity']['radial']['knots'],
    material['thermal conductivity']['radial']['values'])
kappa_rad = spl.spline_as_ufl(spline,
                material['thermal conductivity']['radial']['knots'])

spline = spl.gen_hermite_spline(
    material['thermal conductivity']['axial']['knots'],
    material['thermal conductivity']['axial']['values'])
kappa_ax = spl.spline_as_ufl(spline,
                material['thermal conductivity']['axial']['knots'])


def kappa(theta):
    return as_matrix([[kappa_rad(theta), Constant("0.0")],
                      [Constant("0.0"), kappa_ax(theta)]])


def s(theta):
    return c(theta) * rho(theta)


def laser_bc(intensity):
    return laser_pd * intensity


def cooling_bc(theta):
    return - 20 * (theta-theta_amb)\
           - 2.26 * 10**(-9) * (theta**4-theta_amb**4)


def u(t, t1=0.005, t2=0.010):
    if t < t1:
        return 1.
    elif t < t2:
        return (t2-t)/(t2-t1)
    else:
        return 0.
 

def a(u, u_, v, intensity):

    u_m = implicitness * u_ + (1-implicitness) * u

    a = s(u) * (u_ - u) * v * x[0] * dx\
      + dt * inner(kappa(u) * grad(u_m), grad(v)) * x[0] * dx\
      - dt * laser_bc(intensity) * v * x[0] * ds(1)\
      - dt * cooling_bc(u_m) * v * x[0] * (ds(1) + ds(2))

    return a


def solve_forward(control, theta_init=project(theta_amb, V)):
    '''Calculates the solution to the forward problem with the given control.

    For further details, see `indexing diagram`.

    Parameters:
        control: ndarray
            The laser power coefficient for every time step. 
        theta_init: Function(V)
            The initial state of the temperature.

    Returns:
        evolution: ndarray
            The coefficients of the calculated solution in the basis of
            the space V at each time step including the initial state.
            
    '''

    theta = Function(V)
    theta_ = Function(V)
    v = TestFunction(V)

    theta.assign(theta_init)

    Nt = len(control)
    evolution = np.zeros((Nt+1, len(V.dofmap().dofs())))
    evolution[0,:] = theta.vector().get_local()

    for k in range(Nt):
        F = a(theta, theta_, v, Constant(control[k]))
        solve(F == 0, theta_)
        evolution[k+1,:] = theta_.vector().get_local()
        theta.assign(theta_)

    return evolution


def save_as_npy(evolution, filename='evolution.npy'):
    np.save(filename, evolution)

def save_as_pvd(evolution, filename='evolution.pvd'):
    outfile = File(filename)
    theta = Function(V)
    for k in range(Nt+1):
        theta.vector().set_local(evolution[k])
        theta.rename("theta", "temperature")
        outfile << theta, k


def solve_adjoint(evolution, control):
    '''Calculates the solution to the adjoint problem.

    The solution to the adjoint equation is calculated using the explicitly
    given evolution (solution to the forward problem) and control.
    The objective function is provided implicitly and represented in the code
    by J_expression.

    For better understanding of the indeces see docs/indexing-diagram.txt.

    Parameters:
        evolution: ndarray
            The coefficients of the solution to the corresponding forward
            problem in the basis of the space V (see solve_forward).
        control: ndarray
            The laser power coefficient for every time step. 

    Returns:
        evolution_adj: ndarray
            The coefficients of the calculated adjoint solution in the basis of
            the space V.
            
    '''

    p_prev = TrialFunction(V)
    p_next = Function(V)
    p = Function(V)
    theta_next = Function(V)
    theta_prev = Function(V)
    theta_next_ = Function(V)
    v = TestFunction(V)

    Nt = len(control)
    evolution_adj = np.zeros((Nt+1, len(V.dofmap().dofs())))
    evolution_adj[Nt,:] = p_next.vector().get_local()

    # PointSource magnitute precalculation
    sum_ = 0
    for k in range(1,Nt+1):
        theta_next.vector().set_local(evolution[k])
        sum_ += np.float_power(theta_next(target_point), pow_)
    norm = np.float_power(sum_, 1/pow_)
    M = beta_w * (norm - threshold_temp)\
      * np.float_power(sum_, 1/pow_-1)

    # solve backward, i.e. p_next -> p_prev
    theta_next.vector().set_local(evolution[Nt])
    for k in range(Nt,0,-1):
        theta_prev.vector().set_local(evolution[k-1])

        F = a(theta_prev, theta_next, p_prev, Constant(control[k-1]))

        if k < Nt:
            # is it correct that the next line can be omitted?
            # theta_next_.vector().set_local(evolution[k+1])
            F += a(theta_next, theta_next_, p_next, Constant(control[k]))

        dF = derivative(F,theta_next,v)
        
        # for k==Nt rhs(dF) is void which leads to a ValueError
        try:
            A, b = assemble_system(lhs(dF), rhs(dF))
        except ValueError:
            A, b = assemble_system(lhs(dF), Constant(0)*v*dx)

        M_ = np.float_power(theta_next(target_point), pow_-1)
        ps = PointSource(V, target_point, -M*M_)
        ps.apply(b)
        solve(A, p.vector(), b)
 
        evolution_adj[k-1,:] = p.vector().get_local()
        p_next.assign(p)

        theta_next_.assign(theta_next)
        theta_next.assign(theta_prev)

    return evolution_adj


def Dj(evolution_adj, control):
    '''Calculates the gradient of the cost functional for the given control.

    For further details, see `indexing diagram`.

    Parameters:
        evolution_adj: ndarray
            The evolution in time of the adjoint state.
        control: ndarray
            The laser power coefficient for every time step. 

    Returns:
        Dj: ndarray
            The gradient of the cost functional.
    '''

    p = Function(V)
    z = np.zeros(Nt)

    for i in range(Nt):
        p.vector().set_local(evolution_adj[i])
        z[i] = assemble(p * x[0] * ds(1))
    
    # Dj = alpha * (control-control_ref) - laser_pd*z
    Dj = - laser_pd*z

    return Dj


def gradient_descent(control, init, iter_max=100, s=512.):
    '''Calculates the optimal control.

    Parameters:
        control: ndarray
            Initial guess.
        iter_max: integer
            The maximal allowed number of iterations.

    Returns:
        control_optimal: ndarray

    '''

    try:
        # TODO: change the breaking condition

        evolution = solve_forward(control, init)
        cost = J(evolution, control)
        cost_next = cost

        controls_iterations = []
        controls_iterations.append(control)

        print('{:>4} {:>12} {:>14} {:>14}'.format('i', 's', 'j', 'norm'))

        for i in range(iter_max):
            
            evolution_adj = solve_adjoint(evolution, control)
            D = Dj(evolution_adj, control)
            norm = dt * np.sum(D**2)
            
            if norm < tolerance:
                print('norm = {} < tolerance'.format(norm))
                break

            first_try = True
            while (cost_next >= cost) or first_try:
                control_next = np.clip(control - s*D, 0, 1)
                evolution_next = solve_forward(control_next, init)
                cost_next = J(evolution_next, control)
                print('{:4} {:12.6f} {:14.7e} {:14.7e}'.\
                    format(i, s, cost_next, norm))
                if not first_try: s /= 2
                first_try = False

            s *= 2
            control = control_next
            cost = cost_next
            evolution = evolution_next
            controls_iterations.append(control)

    except KeyboardInterrupt:
        print('Interrupted by user...')

    return controls_iterations


# def J(evolution, control, as_vector=False, **kwargs):
#     '''Calculates the cost functional.'''

#     # cost = 0.
#     theta = Function(V)
#     theta_ = Function(V)

#     vector = np.zeros(Nt)

#     theta.vector().set_local(evolution[0])
#     for k in range(Nt):
#         theta_.vector().set_local(evolution[k+1])
#         # value = dt * assemble(velocity(theta, theta_)**2 * x[0] * dx)
#         # cost += value
#         vector[k] += dt * assemble(velocity(theta, theta_)**2 * x[0] * dx)
#         vector[k] += dt * assemble(liquidity(theta, theta_)**2 * x[0] * dx)
#         theta.assign(theta_)

#     # vector += 0.5 * alpha * (control-control_ref)**2

#     if as_vector:
#         return vector
#     else:



def gradient_test(control, n=15, diff_type='forward', eps_init=.1):
    '''Checks the accuracy of the calculated gradient Dj.

    The scalar product (Dj,direction) is calculated and
    compared to the finite difference expression for J w.r.t. direction,
    the absolute error and the relative error are calculated.

    Every iteration epsilon is divided by two.

    Parameters:
        control: ndarray
            Control used for testing.
        n: integer
            Number of tests.
        diff_type: 'forward' (default) or 'two_sided'
            The exact for of the finite difference expression.
        eps_init: float
            The initial value of epsilon.

    Returns:
        epsilons: array-like
            Epsilons used for testing.
        deltas: array-like
            Relative errors.

    '''

    evo = solve_forward(control)
    evo_adj = solve_adjoint(evo, control)
    time_space = np.linspace(0, T, num=Nt, endpoint=True)
    # np.random.seed(0)
    direction = np.random.rand(Nt)
    norm = np.sqrt(dt * np.sum(direction**2))
    direction /= norm
    direction *= .0005 * T


    D = Dj(evo_adj, control)
    print('{:>16}{:>16}{:>16}{:>16}{:>16}'.\
        format('epsilon', '(Dj,direction)', 'finite diff', 'absolute error',
               'relative error'))

    epsilons = [eps_init * 2**-k for k in range(n)]
    deltas = []

    scalar_product = dt * np.sum(D*direction)

    for eps in epsilons:
        
        if diff_type == 'forward':
            control_eps = control + eps * direction
            evo_eps = solve_forward(control_eps)
            diff = (J(evo_eps, control_eps) - J(evo, control)) / eps
        elif diff_type == 'two_sided':
            control_plus_eps = control + eps * direction
            evo_plus_eps = solve_forward(control_plus_eps)
            control_minus_eps = control - eps * direction
            evo_minus_eps = solve_forward(control_minus_eps)
            diff = (J(evo_plus_eps, control_plus_eps)
                       - J(evo_minus_eps, control_minus_eps)) / (2 * eps)            
        
        delta_abs = scalar_product - diff
        delta_rel = delta_abs / scalar_product
        deltas.append(delta_rel)
        print('{:16.8e}{:16.8e}{:16.8e}{:16.8e}{:16.8e}'.\
            format(eps, scalar_product, diff, delta_abs, delta_rel))

    return epsilons, deltas


def J_welding(evolution, control):
    sum_ = 0
    theta = Function(V)
    for k in range(1,Nt+1):
        theta.vector().set_local(evolution[k])
        sum_ += np.float_power(theta(target_point), pow_)
    norm = np.float_power(sum_, 1/pow_)
    result = .5 * beta_w * (norm - threshold_temp)**2

    return result

J = J_welding