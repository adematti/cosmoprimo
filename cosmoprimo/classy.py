"""Cosmological calculation with the Boltzmann code CLASS."""

import numpy as np
import pyclass
from pyclass import ClassParserError

from .cosmology import BaseEngine
from .interpolator import PowerSpectrumInterpolator1D, PowerSpectrumInterpolator2D


class ClassEngine(pyclass.ClassEngine, BaseEngine):

    """Engine for the Boltzmann code CLASS."""
    name = 'class'

    def __init__(self, *args, **kwargs):
        BaseEngine.__init__(self, *args, **kwargs)
        params = self._params.copy()
        extra_params = self.extra_params.copy()
        lensing = params.pop('lensing')
        params['k_pivot'] = params['k_pivot']
        params['lensing'] = 'yes' if lensing else 'no'
        params['A_s'] = BaseEngine._get_A_s_fid(self)
        if 'sigma8' in params: del params['sigma8']
        params['modes'] = ','.join(params['modes'])
        if 't' not in params['modes']: del params['r']
        params['z_max_pk'] = max(params.pop('z_pk'))
        params['P_k_max_h/Mpc'] = params.pop('kmax_pk')
        params['l_max_scalars'] = params.pop('ellmax_cl')
        if params['non_linear']:
            params['z_max_pk'] = min(params['z_max_pk'], 2.)  # otherwise error
            non_linear = params['non_linear']
            if non_linear in ['mead', 'hmcode']:
                params['non_linear'] = 'hmcode'
            elif non_linear in ['halofit']:
                params['non_linear'] = 'halofit'
            else:
                raise ClassParserError('Unknown non-linear code {}'.format(non_linear))
        else:
            del params['non_linear']
        params['N_ncdm'] = self['N_ncdm']
        params['T_ncdm'] = params.pop('T_ncdm_over_cmb')
        if not params['N_ncdm']:
            params.pop('m_ncdm')
            params.pop('T_ncdm')
        if self._has_fld:
            params['Omega_Lambda'] = 0.  # will force non-zero Omega_fld
        else:
            for name in ['w0_fld', 'wa_fld']: del params[name]
        params.update(extra_params)
        super(ClassEngine, self).__init__(params=params)
        # print(self.get_params_str())


Background = pyclass.Background
Thermodynamics = pyclass.Thermodynamics
Transfer = pyclass.Transfer
Perturbations = pyclass.Perturbations


class Primordial(pyclass.Primordial):

    def __init__(self, engine):
        super(Primordial, self).__init__(engine)
        self._rsigma8 = engine._rescale_sigma8()

    @property
    def A_s(self):
        r"""Scalar amplitude of the primordial power spectrum at :math:`k_\mathrm{pivot}`, unitless."""
        return super(Primordial, self).A_s * self._rsigma8**2

    @property
    def ln_1e10_A_s(self):
        r""":math:`\ln(10^{10}A_s)`, unitless."""
        return np.log(1e10 * self.A_s)

    def pk_k(self, k, mode='scalar'):
        r"""
        The primordial spectrum of curvature perturbations at ``k``, generated by inflation, in :math:`(\mathrm{Mpc}/h)^{3}`.
        For scalar perturbations this is e.g. defined as:

        .. math::

            \mathcal{P_R}(k) = A_s \left (\frac{k}{k_0} \right )^{n_s - 1 + 1/2 \ln(k/k_0) (dn_s / d\ln k)}

        See also: eq. 2 of `this reference <https://arxiv.org/abs/1303.5076>`_.

        Parameters
        ----------
        k : array_like
            Wavenumbers, in :math:`h/\mathrm{Mpc}`.

        mode : string, default='scalar'
            'scalar', 'vector' or 'tensor' mode.

        Returns
        -------
        pk : array, dict
            The primordial power spectrum if only one type of initial conditions (typically adiabatic),
            else dictionary of primordial power spectra corresponding to the tuples of initial conditions.
        """
        toret = super(Primordial, self).pk_k(k, mode=mode)
        if isinstance(toret, dict):
            for key, value in toret.items():
                toret[key] = value * self._rsigma8**2
        else:
            toret *= self._rsigma8**2
        return toret

    def pk_interpolator(self, mode='scalar'):
        """
        Return power spectrum interpolator.

        Parameters
        ----------
        mode : string, default='scalar'
            'scalar', 'vector' or 'tensor' mode.

        Returns
        -------
        interp : PowerSpectrumInterpolator1D, dict
            :class:`PowerSpectrumInterpolator1D` instance if only one type of initial conditions (typically adiabatic),
            else dictionary of class:`PowerSpectrumInterpolator1D` corresponding to the tuples of initial conditions.
        """
        toret = self.pk_k(1e-3, mode=mode)
        if isinstance(toret, dict):
            return {ic: PowerSpectrumInterpolator1D.from_callable(pk_callable=lambda k: self.pk_k(k, mode=mode)[ic]) for ic in toret}
        return PowerSpectrumInterpolator1D.from_callable(pk_callable=lambda k: self.pk_k(k, mode=mode))

    def table(self):
        r"""
        Return primordial table.

        Returns
        -------
        data : array
            Structured array containing thermodynamics data.
        """
        table = super(Primordial, self).table()
        for name in table.dtype.names:
            if not name.startswith('k'):
                table[name] *= self._rsigma8**2


class Harmonic(pyclass.Harmonic):

    def __init__(self, engine):
        super(Harmonic, self).__init__(engine)
        self._rsigma8 = engine._rescale_sigma8()

    def unlensed_table(self, ellmax=-1, of=None):
        r"""
        Return table of unlensed :math:`C_{\ell}` (i.e. CMB power spectra without lensing and lensing potentials), unitless.

        Parameters
        ----------
        ellmax : int, default=-1
            Maximum :math:`\ell` desired. If negative, is relative to the requested maximum :math:`\ell`.

        of : list, default=None
            List of outputs, ['tt','ee','bb','te','pp','tp','ep']. If ``None``, return all computed outputs.

        Returns
        -------
        cell : array
            Structured array.

        Note
        ----
        Normalisation is :math:`C_{\ell}` rather than :math:`\ell(\ell+1)C_{\ell}/(2\pi)` (or :math:`\ell^{2}(\ell+1)^{2}/(2\pi)` in the case of
        the lensing potential ``pp`` spectrum).
        Usually multiplied by CMB temperature in :math:`\mu K`.
        """
        table = super(Harmonic, self).unlensed_table(ellmax=ellmax, of=of)
        for name in table.dtype.names:
            if not name.startswith('ell'):
                table[name] *= self._rsigma8**2
        return table

    def lensed_table(self, ellmax=-1, of=None):
        r"""
        Return table of lensed :math:`C_{\ell}`, unitless.

        Parameters
        ----------
        ellmax : int, default=-1
            Maximum :math:`\ell` desired. If negative, is relative to the requested maximum :math:`\ell`.

        of : list, default=None
            List of outputs, ['tt','ee','bb','pp','te','tp']. If ``None``, return all computed outputs.

        Returns
        -------
        cell : array
            Structured array.
        """
        table = super(Harmonic, self).lensed_table(ellmax=ellmax, of=of)
        for name in table.dtype.names:
            if not name.startswith('ell'):
                table[name] *= self._rsigma8**2
        return table


class Fourier(pyclass.Fourier):

    def __init__(self, engine):
        super(Fourier, self).__init__(engine)
        self._rsigma8 = engine._rescale_sigma8()

    @property
    def sigma8_m(self):
        r"""Current r.m.s. of matter perturbations in a sphere of :math:`8 \mathrm{Mpc}/h`, unitless."""
        return super(Fourier, self).sigma8_m * self._rsigma8

    @property
    def sigma8_cb(self):
        r"""Current r.m.s. of cold dark matter + baryons perturbations in a sphere of :math:`8 \mathrm{Mpc}/h` unitless."""
        return super(Fourier, self).sigma8_cb * self._rsigma8

    def sigma_rz(self, r, z, of='delta_m', **kwargs):
        r"""Return the r.m.s. of `of` perturbations in sphere of :math:`r \mathrm{Mpc}/h`."""
        return self.pk_interpolator(non_linear=False, of=of, **kwargs).sigma_rz(r, z)

    def sigma8_z(self, z, of='delta_m'):
        r"""Return the r.m.s. of `of` perturbations in sphere of :math:`8 \mathrm{Mpc}/h`."""
        return self.sigma_rz(8., z, of=of)

    def pk_kz(self, k, z, non_linear=False, of='m'):
        r"""
        Return power spectrum, in :math:`(\mathrm{Mpc}/h)^{3}`, using original CLASS routine.

        Parameters
        ----------
        k : array_like
            Wavenumbers, in :math:`h/\mathrm{Mpc}`.

        z : array_like
            Redshifts.

        non_linear : bool, default=False
            Whether to return the non_linear power spectrum (if requested in parameters, with 'non_linear': 'halofit' or 'mead').

        of : string, default='delta_m'
            Perturbed quantities.
            Either 'delta_m' for matter perturbations or 'delta_cb' for cold dark matter + baryons perturbations.

        Returns
        -------
        pk : array
            Power spectrum array of shape (len(k),len(z)).
        """
        return super(Fourier, self).pk_kz(k, z, non_linear=non_linear, of=of) * self._rsigma8**2

    def table(self, non_linear=False, of='delta_m'):
        r"""
        Return power spectrum table, in :math:`(\mathrm{Mpc}/h)^{3}`.

        Parameters
        ----------
        non_linear : bool, default=False
            Whether to return the non_linear power spectrum (if requested in parameters, with 'non_linear': 'halofit' or 'mead').
            Computed only for ``of == 'delta_m'`` or 'delta_cb'.

        of : string, tuple, default='delta_m'
            Perturbed quantities.
            Either 'delta_m' for matter perturbations or 'delta_cb' for cold dark matter + baryons perturbations will use precomputed spectra.
            Else, e.g. ('delta_m','theta_cb') for the cross matter density - cold dark matter + baryons velocity power spectra, are computed on-the-fly.

        Returns
        -------
        k : array
            Wavenumbers.

        z : array
            Redshifts.

        pk : array
            Power spectrum array of shape (len(k),len(z)).
        """
        k, z, pk = super(Fourier, self).table(non_linear=non_linear, of=of)
        pk *= self._rsigma8**2
        return k, z, pk

    def pk_interpolator(self, non_linear=False, of='delta_m', **kwargs):
        """
        Return :class:`PowerSpectrumInterpolator2D` instance.

        Parameters
        ----------
        non_linear : bool, default=False
            Whether to return the non_linear power spectrum (if requested in parameters, with 'non_linear': 'halofit' or 'mead').
            Computed only for ``of == 'delta_m'`` or 'delta_cb'.

        of : string, tuple, default='delta_m'
            Perturbed quantities.
            Either 'delta_m' for matter perturbations or 'delta_cb' for cold dark matter + baryons perturbations will use precomputed spectra.
            Else, e.g. ('delta_m','theta_cb') for the cross matter density - cold dark matter + baryons velocity power spectra, are computed on-the-fly.

        kwargs : dict
            Arguments for :class:`PowerSpectrumInterpolator2D`.
        """
        ka, za, pka = self.table(non_linear=non_linear, of=of)
        return PowerSpectrumInterpolator2D(ka, za, pka, **kwargs)
