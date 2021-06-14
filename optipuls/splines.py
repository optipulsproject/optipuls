'''Provides a set of classes to define the PDE coefficients as splines. 

Classes:
    Spline:
        A class for splines difened on the whole real line.
    HermiteSpline(Spline):
        Implements a container for a Hermite spline.
    NaiveHermiteSpline(HermiteSpline):
        Implements a naive hermite spline which derivatives the knots are not
        given but calculated using numpy.gradient.

Functions:
    hermine_interpolating_polynomial:
        Generates Hermite interpolating polynomial for the given interval,
        values, and derivatives at the ends.
    load:
        Loads a previously dumped Spline object.

'''

import numbers

import numpy as np
from numpy.polynomial import Polynomial


# Hermite basis polynomials
h00 = np.array([1, 0, -3,  2])  # 1 - 3*x^2 + 2*x^3
h10 = np.array([0, 1, -2,  1])  # x - 2*x^2 + x^3
h01 = np.array([0, 0,  3, -2])  # 3*x^2 - 2*x^3
h11 = np.array([0, 0, -1,  1])  # - *x^2 + x^3


class Spline:
    '''A class for splines difened on the whole real line.

            x[0]       x[1]       x[2]      x[n-1]
    ----------+----------+----------+- - - - -+----------
      pol[0]     pol[1]     pol[2]               pol[n]


    Attributes:
        knots: [float]
            [x[0], x[1], ..., x[n-1]]
        coef_array: [[float]]
            [
                pol[0].coef,
                pol[1].coef,
                ...
                po[n].coef
            ]

    '''

    def __init__(self, knots, coef_array):
        if not np.all(knots[:-1] <= knots[1:]):
            raise ValueError('knots are not sorted')
        if len(coef_array) != len(knots) + 1:
            raise ValueError('dimension inconsistency')

        self.knots = knots
        self.coef_array = coef_array

    def __call__(self, x):
        for (knot, coef) in zip(self.knots, self.coef_array):
            if x < knot:
                return Polynomial(coef)(x)
        else:
            coef = self.coef_array[-1]
            return Polynomial(coef)(x)

    def derivative(self):
        knots = self.knots
        coef_array = np.array(
                [Polynomial(coef).deriv().coef
                for coef in self.coef_array])
        return Spline(knots, coef_array)

    def dump(self, file='spline.npz'):
        '''Dumps into a file.'''
        np.savez(file, knots=self.knots, coef_array=self.coef_array)


class HermiteSpline(Spline):
    '''Implements a container for a Hermite spline.

    References:
    - https://en.wikipedia.org/wiki/Cubic_Hermite_spline

    '''

    def __init__(self, knots, values, derivatives,
                 extrapolation_left='constant',
                 extrapolation_right='constant'):
        if not np.all(knots[:-1] <= knots[1:]):
            raise ValueError('knots are not sorted')
        if not len(knots) == len(values):
            raise ValueError('dimension inconsistency')
        if not len(knots) == len(derivatives):
            raise ValueError('dimension inconsistency')
        
        coef_array = np.zeros((len(knots)+1, 4), dtype=float)

        for i in range(len(knots) - 1):
            p = hermine_interpolating_polynomial(
                    knots=[knots[i], knots[i+1]],
                    values=[values[i], values[i+1]],
                    derivatives=[derivatives[i], derivatives[i+1]])
            coef_array[i+1, :len(p.coef)] = p.coef

        # assigning the left and the right polynomials based on the preferred
        # extrapolation method
        if extrapolation_left=='constant':
            coef_array[0] = values[0], 0, 0, 0
        elif extrapolation_left=='linear':
            k = Polynomial(coef_array[1]).deriv()(knots[0])
            b = values[0] - k*knots[0]
            coef_array[0] = b, k, 0, 0

        if extrapolation_right=='constant':
            coef_array[-1] = values[-1], 0, 0, 0
        elif extrapolation_right=='linear':
            k = Polynomial(coef_array[-2]).deriv()(knots[-1])
            b = values[-1] - k*knots[-1]
            coef_array[-1] = b, k, 0, 0
        
        self.knots = knots
        self.coef_array = coef_array


class NaiveHermiteSpline(HermiteSpline):
    '''Implements a naive Hermite spline which derivatives the knots are not
    given but calculated using numpy.gradient.

    '''

    def __init__(self, knots, values,
                 extrapolation_left='constant',
                 extrapolation_right='constant'):

        derivatives = np.gradient(values, knots)

        HermiteSpline.__init__(self, knots, values, derivatives,
                               extrapolation_left, extrapolation_right)


def hermine_interpolating_polynomial(knots, values, derivatives):
    '''Generates Hermite interpolating polynomial.

    Parameters:
        knots: [float]
            Two points on the x-axis.
        values: [float]
            The desired values at the given points.
        derivatives: [float]
            The desired derivatives at the given points.

    Returns:
        polynomial: Polynomial
            The generated Hermite interpolating polynomial.

    '''

    x0, x1 = knots
    p0, p1 = values
    m0, m1 = derivatives

    coef_unscaled = h00*p0 + h01*p1 + (h10*m0 + h11*m1) * (x1 - x0)

    # domain and window together with convert() from numpy.polynomial
    # are used as a linear mapping t = (x - knot) / (knot_ - knot) 
    polynomial = Polynomial(coef_unscaled, domain=[x0, x1], window=[0,1])
    polynomial = polynomial.convert()
    
    return polynomial


def load(file='spline.npz'):
    '''Loads a previously dumped Spline object.

    Parameters:
        file: str or a file object

    Returns:
        spline: Spline

    '''
    npz_obj = np.load(file)
    return Spline(npz_obj['knots'], npz_obj['coef_array'])
