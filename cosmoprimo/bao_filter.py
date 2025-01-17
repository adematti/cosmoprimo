"""
Different techniques to extra BAO from the power spectrum of correlation function.
For the power spectrum, the most accurate one is :class:`Wallish2018PowerSpectrumBAOFilter`.
For the correlation function: :class:`Kirkby2013CorrelationFunctionBAOFilter`.
"""

import numpy as np

from .interpolator import PowerSpectrumInterpolator2D, CorrelationFunctionInterpolator2D
from .utils import BaseClass, LeastSquareSolver
from .cosmology import Cosmology, Fourier


class RegisteredPowerSpectrumBAOFilter(type(BaseClass)):

    """Metaclass registering :class:`BasePowerSpectrumBAOFilter`-derived classes."""

    _registry = {}

    def __new__(meta, name, bases, class_dict):
        cls = super().__new__(meta, name, bases, class_dict)
        meta._registry[cls.name] = cls
        return cls


class BasePowerSpectrumBAOFilter(BaseClass, metaclass=RegisteredPowerSpectrumBAOFilter):

    """Base BAO filter for power spectrum."""
    name = 'base'

    def __init__(self, pk_interpolator, cosmo=None, cosmo_fid=None, **kwargs):
        """
        Run BAO filter.

        Parameters
        ----------
        pk_interpolator : PowerSpectrumInterpolator1D, PowerSpectrumInterpolator2D
            Input power spectrum to remove BAO wiggles from.

        cosmo : Cosmology, default=None
            Cosmology instance, which may be used to tune filter settings (e.g.``rs_drag``).

        cosmo_fid : Cosmology, default=None
            Reference cosmology.

        kwargs : dict
            Arguments for :meth:`set_k`.
        """
        self._cosmo_fid = cosmo_fid
        self.pk_interpolator = pk_interpolator
        self.set_k(**kwargs)
        self.set_pk(pk_interpolator, cosmo=cosmo)
        self._prepare()
        self._compute()

    def _prepare(self):
        """Anything that can be done once."""

    def set_k(self, nk=1024):
        """
        Set wavenumbers where to evaluate the power spectrum (between :attr:`pk_interpolator.extrap_kmin` and :attr:`pk_interpolator.extrap_kmax`).

        Parameters
        ----------
        nk : int, default=1024
            Number of wavenumbers.
        """
        self.k = np.geomspace(self.pk_interpolator.extrap_kmin, self.pk_interpolator.extrap_kmax, nk)

    def set_pk(self, pk_interpolator, cosmo=None):
        """Set input power spectrum to remove BAO wiggles from."""
        self._cosmo = cosmo
        self.pk_interpolator = pk_interpolator
        self.is2d = isinstance(pk_interpolator, PowerSpectrumInterpolator2D)
        if self.is2d:
            self.pk = self.pk_interpolator(self.k, self.pk_interpolator.z, ignore_growth=True)
        else:
            self.pk = self.pk_interpolator(self.k)[:, None]

    def __call__(self, pk_interpolator, cosmo=None):
        self.set_pk(pk_interpolator, cosmo=cosmo)
        self._compute()
        if not self.is2d:
            self.pk, self.pknow = self.pk[..., 0], self.pknow[..., 0]
        return self

    @property
    def wiggles(self):
        """Extracted wiggles."""
        return self.pk / self.pknow

    def smooth_pk_interpolator(self, **kwargs):
        """
        Return smooth (i.e. no-wiggle) power spectrum.

        Parameters
        ----------
        kwargs : dict
            Override interpolation and extrapolation settings of :attr:`pk_interpolator`.

        Returns
        -------
        interp : PowerSpectrumInterpolator1D, PowerSpectrumInterpolator2D
            1D or 2D depending on :attr:`pk_interpolator`.
        """
        return self.pk_interpolator.clone(k=self.k, pk=self.pknow, **kwargs)

    def smooth_xi_interpolator(self, **kwargs):
        """
        Return smooth (i.e. no-peak) correlation function using :class:`FFTlog`.

        Parameters
        ----------
        kwargs : dict
            Override interpolation and extrapolation settings of returned correlation function interpolator.

        Returns
        -------
        interp : CorrelationFunctionInterpolator1D, CorrelationFunctionInterpolator2D
            1D or 2D depending on :attr:`pk_interpolator`.
        """
        return self.smooth_pk_interpolator().to_xi(**kwargs)

    @property
    def cosmo(self):
        """Cosmology."""
        if self._cosmo is None:
            self._cosmo = Cosmology()
        return self._cosmo

    @property
    def cosmo_fid(self):
        """Reference cosmology."""
        if self._cosmo_fid is None:
            self._cosmo_fid = Cosmology()
        return self._cosmo_fid

    def rs_drag_ratio(self):
        """If :attr:`cosmo` is provided, return the ratio of its ``rs_drag`` to the fiducial one (from ``Cosmology()``), else 1."""
        if self._cosmo is None:
            return 1.
        if self._cosmo_fid is None:
            rs_drag_fid = 100.91463132327911
        else:
            rs_drag_fid = self.cosmo_fid.get_thermodynamics().rs_drag
        return self.cosmo.get_thermodynamics().rs_drag / rs_drag_fid


class Hinton2017PowerSpectrumBAOFilter(BasePowerSpectrumBAOFilter):
    """
    Power spectrum BAO filter consisting in fitting a high degree polynomial to the input power spectrum in log-log space.

    References
    ----------
    https://github.com/Samreay/Barry/blob/master/barry/cosmology/power_spectrum_smoothing.py

    Note
    ----
    We have hand-tune parameters w.r.t. the reference.
    """
    name = 'hinton2017'

    def __init__(self, pk_interpolator, degree=13, sigma=0.5, weight=0.9, **kwargs):
        """
        Run BAO filter.

        Parameters
        ----------
        pk_interpolator : PowerSpectrumInterpolator1D, PowerSpectrumInterpolator2D
            Input power spectrum to remove BAO wiggles from.

        degree : int, default=13
            Polynomial degree.

        sigma : float, default=0.5
            Standard deviation of the Gaussian kernel that downweights the maximum of the power spectrum relative to the edges.

        weight : float, default=0.9
            Normalisation of the Gaussian kernel.
        """
        self.degree = degree
        self.sigma = sigma
        self.weight = weight
        super(Hinton2017PowerSpectrumBAOFilter, self).__init__(pk_interpolator, **kwargs)

    def _compute(self):
        """Run filter."""
        mask = (self.k > 1e-4) & (self.k < 5.)
        logk = np.log10(self.k[mask])
        logpk = np.log10(self.pk[mask].T)
        maxk = logk[np.argmax(logpk[0], axis=0)]  # here we take just the first one, approximation
        meanlogk = np.mean(logk)
        gauss = np.exp(-0.5 * ((logk - maxk) / self.sigma)**2)
        w = np.ones_like(logk) - self.weight * gauss

        gradient = np.array([(logk - meanlogk)**i for i in range(self.degree)])
        constraint_gradient = np.column_stack([gradient[..., 0], gradient[..., 1] - gradient[..., 0],
                                               gradient[..., 2] - 2. * gradient[..., 1] + gradient[..., 0],
                                               gradient[..., -1], gradient[..., -2] - gradient[..., -1],
                                               gradient[..., -3] - 2. * gradient[..., -2] + gradient[..., -1]])
        lss = LeastSquareSolver(gradient, precision=w**2, constraint_gradient=constraint_gradient, compute_inverse=False)
        lss(logpk, constraint=np.column_stack([logpk[..., 0], logpk[..., 1] - logpk[..., 0],
                                               logpk[..., 2] - 2. * logpk[..., 1] + logpk[..., 0],
                                               logpk[..., -1], logpk[..., -2] - logpk[..., -1],
                                               logpk[..., -3] - 2. * logpk[..., -2] + logpk[..., -1]]))

        self.pknow = self.pk.copy()
        self.pknow[mask] = 10 ** lss.model().T


class SavGolPowerSpectrumBAOFilter(BasePowerSpectrumBAOFilter):
    r"""
    BAO smoothing with Savitzky-Golay filter.

    References
    ----------
    Taken from https://github.com/sfschen/velocileptors/blob/master/velocileptors/EPT/ept_fullresum_fftw.py

    Note
    ----
    Contrary to the reference, we work in :math:`\log(k)` - :math:`\log(k P(k))` space.
    """
    name = 'savgol'

    def _compute(self):
        """Run filter."""
        from scipy.signal import savgol_filter
        # empirical setting of https://github.com/sfschen/velocileptors/blob/master/velocileptors/EPT/cleft_kexpanded_resummed_fftw.py#L37
        nfilter = int(np.ceil(np.log(7) / np.log(self.k[-1] / self.k[-2])) // 2 * 2 + 1)  # filter length ~ log span of one oscillation from k = 0.01
        # self.pknow = np.exp(savgol_filter(np.log(self.pk), nfilter, polyorder=4, axis=0))
        self.pknow = (np.exp(savgol_filter(np.log(self.k * self.pk.T), nfilter, polyorder=4, axis=-1)) / self.k).T
        hnfilter = nfilter // 2
        self.pknow[-hnfilter:] = self.pk[-hnfilter:]


class EHNoWigglePolyPowerSpectrumBAOFilter(BasePowerSpectrumBAOFilter):

    """Remove BAO wiggles using the Eisenstein & Hu no-wiggle analytic formula, emulated with a 6-th order polynomial."""
    name = 'ehpoly'

    def __init__(self, pk_interpolator, krange=(1e-3, 1.), krange_damp=(1e-2, 0.4), sigma_damp=10, rescale_krange=True, cosmo=None, **kwargs):
        """
        Run BAO filter.

        Parameters
        ----------
        pk_interpolator : PowerSpectrumInterpolator1D, PowerSpectrumInterpolator2D
            Input power spectrum to remove BAO wiggles from.

        krange : tuple, default=(1e-3, 1.)
            k-range to fit the Eisenstein & Hu no-wiggle power spectrum to the input one :attr:`pk_interpolator`.

        rescale_krange : bool, default=True
            Whether to rescale ``krange`` and ``krange_damp`` by the ratio of ``rs_drag`` relative to the fiducial cosmology
            (may help robustify the procedure for cosmologies far from the fiducial one).

        cosmo : Cosmology, default=None
            Cosmology instance, used to compute the Eisenstein & Hu no-wiggle power spectrum.

        kwargs : dict
            Arguments for :meth:`set_k`.
        """
        self.krange = krange
        self.rescale_krange = rescale_krange
        super(EHNoWigglePolyPowerSpectrumBAOFilter, self).__init__(pk_interpolator, cosmo=cosmo, **kwargs)

    def _compute(self):
        """Run filter."""
        krange = np.asarray(self.krange)
        if self.rescale_krange:
            krange = krange / self.rs_drag_ratio()
        mask = (self.k >= krange[0]) & (self.k <= krange[1])
        k = self.k[mask]
        ratio = self.pk[mask].T / Fourier(self.cosmo, engine='eisenstein_hu_nowiggle', set_engine=False).pk_interpolator()(k, z=0.)

        gradient = np.array([k**(i - 2) for i in range(6)])
        constraint_gradient = np.column_stack([gradient[..., 0], gradient[..., 1] - gradient[..., 0], gradient[..., -1], gradient[..., -2] - gradient[..., -1]])
        lss = LeastSquareSolver(gradient, precision=k**2, constraint_gradient=constraint_gradient, compute_inverse=False)
        lss(ratio, constraint=np.column_stack([ratio[..., 0], ratio[..., 1] - ratio[..., 0], ratio[..., -1], ratio[..., -2] - ratio[..., -1]]))

        wiggles = np.ones_like(self.pk)
        wiggles[mask] = (ratio / lss.model()).T
        self.pknow = self.pk / wiggles


class Wallish2018PowerSpectrumBAOFilter(BasePowerSpectrumBAOFilter):
    """
    Filter BAO wiggles by sine-transforming the power spectrum to real space (where the BAO is better localized),
    cutting the peak and interpolating with a spline.

    References
    ----------
    https://arxiv.org/pdf/1810.02800.pdf, Appendix D (thanks to Stephen Chen for the reference)
    https://arxiv.org/pdf/1003.3999.pdf

    Note
    ----
    We have hand-tuned parameters w.r.t. the reference.
    """
    name = 'wallish2018'

    def _compute(self):
        """Run filter."""
        from scipy import fftpack, interpolate
        k = np.linspace(self.pk_interpolator.extrap_kmin, 2., 4096)
        if self.is2d:
            pk = self.pk_interpolator(k, self.pk_interpolator.z, ignore_growth=True)
        else:
            pk = self.pk_interpolator(k)[:, None]
        kpk = np.log(k[:, None] * pk)
        kpkffted = fftpack.dst(kpk, type=2, axis=0, norm='ortho', overwrite_x=False)
        even = kpkffted[::2]
        odd = kpkffted[1::2]

        xeven, xodd = 1 + np.arange(even.shape[0]), 1 + np.arange(odd.shape[0])
        spline_even = interpolate.CubicSpline(xeven, even, axis=0, bc_type='clamped', extrapolate=False)
        # dd_even = ndimage.uniform_filter1d(spline_even(xeven,nu=2), 3, axis=0, mode='reflect')
        dd_even = spline_even(xeven, nu=2)
        spline_odd = interpolate.CubicSpline(xodd, odd, axis=0, bc_type='clamped', extrapolate=False)
        # dd_odd = ndimage.uniform_filter1d(spline_odd(xodd,nu=2), 3, axis=0, mode='reflect')
        dd_odd = spline_odd(xodd, nu=2)
        self._even = even  # in case one wants to check everything is ok
        self._odd = odd
        self._dd_even = dd_even
        self._dd_odd = dd_odd
        margin_first = 20
        margin_second = 5
        offset_even = offset_odd = (-10, 20)

        def smooth_even_odd(even, odd, dd_even, dd_odd):
            argmax_even = dd_even[margin_first:].argmax() + margin_first
            argmax_odd = dd_odd[margin_first:].argmax() + margin_first
            ibox_even = (argmax_even + offset_even[0], argmax_even + margin_second + dd_even[argmax_even + margin_first:].argmax() + offset_even[1])
            ibox_odd = (argmax_odd + offset_odd[0], argmax_odd + margin_second + dd_odd[argmax_odd + margin_second:].argmax() + offset_odd[1])
            mask_even = np.ones_like(even, dtype=np.bool_)
            mask_even[ibox_even[0]:ibox_even[1] + 1] = False
            mask_odd = np.ones_like(odd, dtype=np.bool_)
            mask_odd[ibox_odd[0]:ibox_odd[1] + 1] = False
            spline_even = interpolate.CubicSpline(xeven[mask_even], even[mask_even] * xeven[mask_even]**2, axis=-1, bc_type='clamped', extrapolate=False)
            spline_odd = interpolate.CubicSpline(xodd[mask_odd], odd[mask_odd] * xodd[mask_odd]**2, axis=-1, bc_type='clamped', extrapolate=False)
            return spline_even(xeven) / xeven**2, spline_odd(xodd) / xodd**2

        for iz in range(self.pk.shape[-1]):
            even[:, iz], odd[:, iz] = smooth_even_odd(even[:, iz], odd[:, iz], dd_even[:, iz], dd_odd[:, iz])

        self._even_now = even
        self._odd_now = odd
        merged = np.empty_like(kpkffted)
        merged[::2] = even
        merged[1::2] = odd
        kpknow = fftpack.idst(merged, type=2, axis=0, norm='ortho', overwrite_x=False)
        pknow = np.exp(kpknow) / k[..., None]

        mask = (k > 1e-2) & (k < 1.5)
        k, pknow = k[mask], pknow[mask]
        mask_left, mask_right = self.k < 5e-4, self.k > 2.
        k = np.concatenate([self.k[mask_left], k, self.k[mask_right]], axis=0)
        pknow = np.concatenate([self.pk[mask_left], pknow, self.pk[mask_right]], axis=0)
        pknow = interpolate.CubicSpline(k, pknow, axis=0, bc_type='clamped', extrapolate=False)(self.k)
        tophat = self._tophat(self.k, kmax=1., scale=20.)[..., None]
        wiggles = (self.pk / pknow - 1.) * tophat + 1.
        self.pknow = self.pk / wiggles

    @staticmethod
    def _tophat(k, kmax=1, scale=1):
        """Tophat Gaussian kernel."""
        tophat = np.ones_like(k)
        mask = k > kmax
        tophat[mask] *= np.exp(-scale**2 * (k[mask] / kmax - 1.)**2)
        return tophat


class Brieden2022PowerSpectrumBAOFilter(BasePowerSpectrumBAOFilter):
    """
    Filter BAO wiggles by averaging the minima and maxima of the wiggles.

    References
    ----------
    https://arxiv.org/abs/2204.11868, Appendix D (thanks to Samuel Brieden for the reference)
    """
    name = 'brieden2022'

    def _prepare(self):
        self.kmask_fid = (self.k >= 1e-3) & (self.k <= 1.)
        self.k_fid = self.k[self.kmask_fid]
        try:
            pk_fid = Fourier(self.cosmo_fid).pk_interpolator()(self.k_fid, z=0.)  # to cope with A_s-parameterized E&H
        except TypeError:
            pk_fid = Fourier(self.cosmo_fid).pk_interpolator()(self.k_fid, z=0.)
        pknow_fid = Fourier(self.cosmo_fid, engine='eisenstein_hu_nowiggle', set_engine=False).pk_interpolator()(self.k_fid, z=0.)
        ratio = pk_fid / pknow_fid
        gradient = np.array([self.k_fid**(i - 1) for i in range(4)])
        constraint_gradient = np.column_stack([gradient[..., 0], gradient[..., 1] - gradient[..., 0], gradient[..., -1], gradient[..., -2] - gradient[..., -1]])
        lss = LeastSquareSolver(gradient, precision=self.k_fid**2, constraint_gradient=constraint_gradient, compute_inverse=False)
        lss(ratio, constraint=[ratio[..., 0], ratio[..., 1] - ratio[..., 0], ratio[..., -1], ratio[..., -2] - ratio[..., -1]])
        self.pknow_correction = lss.model()[:, None]
        self.ratio_fid = ratio[:, None] / self.pknow_correction
        ik0 = np.searchsorted(self.k_fid, 0.02, side='right') + 1
        self.ik_fid_peaks = []
        from scipy import signal
        for si in [1., -1.]:
            ix = signal.find_peaks(si * self.ratio_fid[ik0:, 0])[0] + ik0  # here we take just the first one, approximation
            ix = np.concatenate([[0]] * (ix[0] > 0) + [ix] + [[-1]] * (ix[-1] < self.k_fid.size - 1), axis=0)
            self.ik_fid_peaks.append(ix)
        self.ratio_now_fid = self._interp(*self.ik_fid_peaks, self.k_fid, self.ratio_fid)

    @staticmethod
    def _interp(ixh, ixl, x, y, kind=2):
        from scipy import interpolate
        toret = 0.
        for ix in [ixh, ixl]:
            toret += interpolate.interp1d(x[ix], y[ix], kind=kind, axis=0, fill_value='extrapolate', assume_sorted=True)(x)
        return toret / 2.

    def _compute(self):
        rescale = self.rs_drag_ratio()
        if self.is2d:
            pk = self.pk_interpolator(self.k_fid / rescale, self.pk_interpolator.z, ignore_growth=True)
        else:
            pk = self.pk_interpolator(self.k_fid / rescale)[:, None]

        pknow = Fourier(self.cosmo, engine='eisenstein_hu_nowiggle', set_engine=False).pk_interpolator()(self.k_fid * rescale, z=0.)[:, None]
        pknow *= self.pknow_correction
        ratio = pk / pknow / self.ratio_fid
        pknow = self._interp(*self.ik_fid_peaks, self.k_fid, ratio) * pknow * self.ratio_now_fid
        pk_interpolator = self.pk_interpolator.clone(k=self.k_fid / rescale, pk=pknow)
        self.pknow = self.pk.copy()
        if self.is2d:
            self.pknow[self.kmask_fid] = pk_interpolator(self.k_fid, self.pk_interpolator.z, ignore_growth=True)
        else:
            self.pknow[self.kmask_fid] = pk_interpolator(self.k_fid)[:, None]


class PeakAveragePowerSpectrumBAOFilter(BasePowerSpectrumBAOFilter):
    """
    Filter BAO wiggles by averaging the minima and maxima of the wiggles at the fiducial positions rescaled by :math:`r_{\mathrm{drag}} / r_{\mathrm{drag}}^{\mathrm{fid}}`.
    A simpler version of :class:`Brieden2022PowerSpectrumBAOFilter`.

    References
    ----------
    https://arxiv.org/abs/2204.11868, Appendix D (thanks to Samuel Brieden for the reference)
    """
    name = 'peakaverage'

    def _prepare(self):
        index = np.flatnonzero((self.k >= 1e-4) & (self.k <= 1.))
        k_fid = self.k[index]
        try:
            pk_fid = Fourier(self.cosmo_fid).pk_interpolator()(k_fid, z=0.)  # to cope with A_s-parameterized E&H
        except TypeError:
            pk_fid = Fourier(self.cosmo_fid).pk_interpolator()(k_fid, z=0.)
        pknow_fid = Fourier(self.cosmo_fid, engine='eisenstein_hu_nowiggle', set_engine=False).pk_interpolator()(k_fid, z=0.)
        ratio = pk_fid / pknow_fid
        gradient = np.array([k_fid**(i - 1) for i in range(4)])
        constraint_gradient = np.column_stack([gradient[..., 0], gradient[..., 1] - gradient[..., 0], gradient[..., -1], gradient[..., -2] - gradient[..., -1]])
        lss = LeastSquareSolver(gradient, precision=k_fid**2, constraint_gradient=constraint_gradient, compute_inverse=False)
        lss(ratio, constraint=[ratio[..., 0], ratio[..., 1] - ratio[..., 0], ratio[..., -1], ratio[..., -2] - ratio[..., -1]])
        pknow_correction = lss.model()
        ik0 = np.searchsorted(k_fid, 1e-2, side='right') + 1
        self.k_peaks = []
        from scipy import signal
        for si in [1., -1.]:
            ik = signal.find_peaks(si * ratio[ik0:] / pknow_correction[ik0:])[0] + ik0  # here we take just the first one, approximation
            ik += index[0]
            k = self.k[np.concatenate([np.arange(index[0]), ik, np.arange(max(index[-1], ik[-1] + 1), self.k.size)], axis=0)]
            self.k_peaks.append(k)

    @staticmethod
    def _interp(xh, xl, x, y, kind=2):
        from scipy import interpolate
        toret = 0.
        interp = interpolate.interp1d(x, y, kind=kind, axis=0, fill_value='extrapolate', assume_sorted=True)
        for xx in [xh, xl]:
            yy = interp(xx)
            toret += interpolate.interp1d(xx, yy, kind=kind, axis=0, fill_value='extrapolate', assume_sorted=True)(x)
        return toret / 2.

    def _compute(self):
        rescale = self.rs_drag_ratio()
        pknow = Fourier(self.cosmo, engine='eisenstein_hu_nowiggle', set_engine=False).pk_interpolator()(self.k)[:, None]
        self.pknow = self._interp(self.k_peaks[0] / rescale, self.k_peaks[1] / rescale, self.k, self.pk / pknow) * pknow


class RegisteredCorrelationFunctionBAOFilter(type(BaseClass)):

    """Metaclass registering :class:`BaseCorrelationFunctionBAOFilter`-derived classes."""

    _registry = {}

    def __new__(meta, name, bases, class_dict):
        cls = super().__new__(meta, name, bases, class_dict)
        meta._registry[cls.name] = cls
        return cls


class BaseCorrelationFunctionBAOFilter(BaseClass, metaclass=RegisteredCorrelationFunctionBAOFilter):

    """Base BAO filter for correlation function."""
    name = 'base'

    def __init__(self, xi_interpolator, cosmo=None, cosmo_fid=None, **kwargs):
        """
        Run BAO filter.

        Parameters
        ----------
        xi_interpolator : CorrelationFunctionInterpolator1D, CorrelationFunctionInterpolator2D
            Input correlation function to remove BAO peak from.

        cosmo : Cosmology, default=None
            Cosmology instance, which may be used to tune filter settings (depending on ``rs_drag``).

        cosmo_fid : Cosmology, default=None
            Reference cosmology.

        kwargs : dict
            Arguments for :meth:`set_s`.
        """
        self._cosmo_fid = cosmo_fid
        self.xi_interpolator = xi_interpolator
        self.set_s(**kwargs)
        self.set_xi(xi_interpolator, cosmo=cosmo)
        self._prepare()
        self._compute()

    def _prepare(self):
        """Anything that can be done once."""

    def set_s(self, ns=1024):
        """
        Set separations where to evaluate the correlation function (between :attr:`xi_interpolator.extrap_smin` and :attr:`xi_interpolator.extrap_smax`).

        Parameters
        ----------
        ns : int, default=1024
            Number of separations.
        """
        self.s = np.geomspace(self.xi_interpolator.extrap_smin, self.xi_interpolator.extrap_smax, ns)

    def set_xi(self, xi_interpolator, cosmo=None):
        """Set input correlation function to remove BAO wiggles from."""
        self._cosmo = cosmo
        self.xi_interpolator = xi_interpolator
        self.is2d = isinstance(xi_interpolator, CorrelationFunctionInterpolator2D)
        if self.is2d:
            self.xi = self.xi_interpolator(self.s, self.xi_interpolator.z, ignore_growth=True)
        else:
            self.xi = self.xi_interpolator(self.s)[:, None]

    def __call__(self, xi_interpolator, cosmo=None):
        self.set_xi(xi_interpolator, cosmo=cosmo)
        self._compute()
        if not self.is2d:
            self.xi, self.xinow = self.xi[..., 0], self.xinow[..., 0]
        return self

    def smooth_xi_interpolator(self, **kwargs):
        """
        Return smooth (i.e. no-peak) correlation function.

        Parameters
        ----------
        kwargs : dict
            Override interpolation settings of :attr:`xi_interpolator`.

        Returns
        -------
        interp : CorrelationFunctionInterpolator1D, CorrelationFunctionInterpolator2D
            1D or 2D depending on :attr:`xi_interpolator`.
        """
        return self.xi_interpolator.clone(s=self.s, xi=self.xinow, **kwargs)

    def smooth_pk_interpolator(self, **kwargs):
        """
        Return smooth (i.e. no-wiggle) power spectrum using :class:`FFTlog`.

        Parameters
        ----------
        kwargs : dict
            Override interpolation and extrapolation settings of return power spectrum interpolator.

        Returns
        -------
        interp : PowerSpectrumInterpolator1D, PowerSpectrumInterpolator2D
            1D or 2D depending on :attr:`pk_interpolator`.
        """
        return self.smooth_xi_interpolator().to_pk(**kwargs)

    @property
    def cosmo(self):
        """Cosmology."""
        if self._cosmo is None:
            self._cosmo = Cosmology()
        return self._cosmo

    @property
    def cosmo_fid(self):
        """Reference cosmology."""
        if self._cosmo_fid is None:
            self._cosmo_fid = Cosmology()
        return self._cosmo_fid

    def rs_drag_ratio(self):
        """If :attr:`cosmo` is provided, return the ratio of its ``rs_drag`` to the fiducial one (from ``Cosmology()``), else 1."""
        if self._cosmo is None:
            return 1.
        if self._cosmo_fid is None:
            rs_drag_fid = 100.91463132327911
        else:
            rs_drag_fid = self.cosmo_fid.get_thermodynamics().rs_drag
        return self.cosmo.get_thermodynamics().rs_drag / rs_drag_fid


class Kirkby2013CorrelationFunctionBAOFilter(BaseCorrelationFunctionBAOFilter):
    """
    Filter BAO peak by cutting the peak and interpolating with 5-order polynomial.

    References
    ----------
    https://arxiv.org/abs/1301.3456
    https://github.com/igmhub/picca/blob/master/bin/picca_compute_pk_pksb.py
    """
    name = 'kirkby2013'

    def __init__(self, xi_interpolator, srange_left=(50., 82.), srange_right=(150., 190.), rescale_sbox=True, cosmo=None, **kwargs):
        """
        Run BAO filter.

        Parameters
        ----------
        xi_interpolator : CorrelationFunctionInterpolator1D, CorrelationFunctionInterpolator2D
            Input correlation function to remove BAO peak from.

        srange_left : tuple
            s-range to fit the polynomial on the left-hand side of the BAO peak.

        srange_right : tuple
            s-range to fit the polynomial on the right-hand side of the BAO peak.

        cosmo : Cosmology
            Cosmology instance, which may be used to tune filter settings (depending on ``rs_drag``).

        rescale_sbox : bool
            Whether to rescale ``srange_left`` and ``srange_right`` by the ratio of ``rs_drag`` relative to the fiducial cosmology
            (may help robustify the procedure for cosmologies far from the fiducial one).

        cosmo : Cosmology
            Cosmology instance, used to compute the Eisenstein & Hu no-wiggle power spectrum.

        kwargs : dict
            Arguments for :meth:`set_s`.
        """
        self.srange_left = srange_left
        self.srange_right = srange_right
        self.rescale_sbox = rescale_sbox
        super(Kirkby2013CorrelationFunctionBAOFilter, self).__init__(xi_interpolator, cosmo=cosmo, **kwargs)

    def _compute(self):
        """Run filter."""
        srange_left, srange_right = np.asarray(self.srange_left), np.asarray(self.srange_right)
        if self.rescale_sbox:
            rescale = self.rs_drag_ratio()
            srange_left, srange_right = srange_left * rescale, srange_right * rescale

        mask = ((self.s >= srange_left[0]) & (self.s <= srange_left[1])) | ((self.s >= srange_right[0]) & (self.s <= srange_right[1]))

        def model(s):
            return np.array([s**(1 - i) for i in range(5)])

        lss = LeastSquareSolver(model(self.s[mask]), precision=1., compute_inverse=False)
        params = lss(self.xi[mask].T)
        mask = (self.s > srange_left[1]) & (self.s < srange_right[0])
        self.xinow = self.xi.copy()
        self.xinow[mask] = params.dot(model(self.s[mask])).T


def PowerSpectrumBAOFilter(pk_interpolator, engine='wallish2018', **kwargs):
    """Run power spectrum BAO filter corresponding to the provided engine."""

    engine = engine.lower()
    try:
        engine = BasePowerSpectrumBAOFilter._registry[engine]
    except KeyError:
        raise ValueError('Power spectrum BAO filter {} is unknown'.format(engine))

    return engine(pk_interpolator, **kwargs)


def CorrelationFunctionBAOFilter(xi_interpolator, engine='kirkby2013', **kwargs):
    """Run correlation function BAO filter corresponding to the provided engine."""

    engine = engine.lower()
    try:
        engine = BaseCorrelationFunctionBAOFilter._registry[engine]
    except KeyError:
        raise ValueError('Correlation function BAO filter {} is unknown'.format(engine))

    return engine(xi_interpolator, **kwargs)
