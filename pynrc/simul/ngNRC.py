"""
ngNRC - NIRCam Detector Noise Simulator

Modification History:

15 Feb 2016, J.M. Leisenring, UA/Steward
    - First Release
21 July 2016, J.M. Leisenring, UA/Steward
    - Updated many things and more for nghxrg (v3.0)
11 Aug 2016, J.M. Leisenring, UA/Steward
    - Modified how the detector and multiaccum info is handled
    - Copied detector and multiaccum classes from pyNRC
    - In the future, we will want to integrate this directly
      so that any changes made in the pyNRC classes are accounted.
21 Feb 2017
    - Add ngNRC to pyNRC code base
    - 
"""
# Necessary for Python 2.6 and later
#from __future__ import division, print_function
from __future__ import absolute_import, division, print_function, unicode_literals

import numpy as np
import datetime, os
from astropy.io import fits
from astropy.table import Table

# HxRG Noise Generator
from . import nghxrg as ng
from pynrc.nrc_utils import pad_or_cut_to_size

#import pdb
from copy import deepcopy

from pynrc import DetectorOps, nrc_header
from pynrc import conf

# # Set log output levels
# # webbpsf and poppy have too many unnecessary warnings
# import logging
# logging.getLogger('nghxrg').setLevel(logging.ERROR)
# 
# _log = logging.getLogger('ngNRC')
# #_log.setLevel(logging.DEBUG)
# #_log.setLevel(logging.INFO)
# _log.setLevel(logging.WARNING)
# #_log.setLevel(logging.ERROR)
# logging.basicConfig(level=logging.WARNING,format='%(name)-10s: %(levelname)-8s %(message)s')

import logging
_log = logging.getLogger('pynrc')

def SCAnoise(det=None, scaid=None, params=None, caldir=None, file_out=None, 
    dark=True, bias=True, out_ADU=False, verbose=False, use_fftw=False, ncores=None,
    **kwargs):
    """NIRCam SCA noise generator
    
    Create a data cube consisting of realistic NIRCam detector noise.

    This is essentially a wrapper for nghxrg.py that selects appropriate values
    for a specified SCA in order to reproduce realistic noise properties similiar
    to those measured during ISIM CV3.

    Parameters
    ----------
    det : object
        Option to specify already existing NIRCam detector object class
        Otherwise, use scaid and params
    scaid : int
        NIRCam SCA number (481, 482, ..., 490)
    params : dictionary
        A set of MULTIACCUM parameters such as:
        
        >>> params = {'ngroup': 2, 'wind_mode': 'FULL', 
        >>>           'xpix': 2048, 'ypix': 2048, 'x0':0, 'y0':0}

        wind_mode can be FULL, STRIPE, or WINDOW
    file_out : str
        Folder name and destination to place optional FITS output. 
        A timestamp will be appended to the end of the file name (and before .fits').
    caldir : str
        Directory location housing the super bias and super darks for each SCA.
    dark : bool
        Use super dark? If True, then reads in super dark slope image.
    bias : bool
        Use super bias? If True, then reads in super bias image.
    out_ADU : bool
        Noise values are calculated in terms of equivalent electrons. This
        gives the option of converting to ADU (True) or keeping in term of e- (False).
        ADU values are converted to 16-bit UINT. Keep in e- if applying to a ramp
        observation then convert combined data to ADU later.

    Returns 
    ----------
    Primary HDU with noise ramp in hud.data and header info in hdu.header.

    Examples 
    ----------
    
    >>> import ngNRC
    >>> params = {'ngroup': 108, 'wind_mode': 'FULL', 
    >>>           'xpix': 2048, 'ypix': 2048, 'x0':0, 'y0':0}
    
    Output to a file:
    
    >>> scaid = 481
    >>> caldir = '/data/darks_sim/nghxrg/sca_images/'
    >>> file_out = '/data/darks_sim/dark_sim_481.fits'
    >>> hdu = ngNRC.SCAnoise(scaid, params, file_out=file_out, caldir=caldir, \
    >>>    dark=True, bias=True, out_ADU=True, use_fftw=False, ncores=None, verbose=False)

    Don't save file, but keep hdu in e- for adding to simulated observation ramp:

    >>> scaid = 481
    >>> caldir = '/data/darks_sim/nghxrg/sca_images/'
    >>> hdu = ngNRC.SCAnoise(scaid, params, file_out=None, caldir=caldir, \
    >>>    dark=True, bias=True, out_ADU=False, use_fftw=False, ncores=None, verbose=False)

    """

    # Extensive testing on both Python 2 & 3 shows that 4 cores is optimal for FFTW
    # Beyond four cores, the speed improvement is small. Those other processors are
    # are better used elsewhere.
    if use_fftw and (ncores is None): ncores = 4

    if det is None:
        wind_mode = params.pop('wind_mode', 'FULL')
        xpix = params.pop('xpix', 2048)
        ypix = params.pop('ypix', 2048)
        x0 = params.pop('x0', 0)
        y0 = params.pop('y0', 0)
        det = DetectorOps(scaid, wind_mode, xpix, ypix, x0, y0, params)
    else:
        scaid = det.scaid


    # Line and frame overheads
    nroh     = det._line_overhead
    nfoh     = det._extra_lines
    nfoh_pix = det._frame_overhead_pix

    # How many total frames (incl. dropped and all) per ramp?
    # Exclude last set of nd2 and nd3 (drops that add nothing)
    ma = det.multiaccum
    naxis3 = ma.nd1 + ma.ngroup*ma.nf + (ma.ngroup-1)*ma.nd2

    # Set bias and dark files
    sca_str = np.str(scaid)
    if caldir is None:
        caldir  = conf.PYNRC_PATH + 'sca_images/'
    bias_file = caldir + 'SUPER_BIAS_'+sca_str+'.FITS' if bias else None
    dark_file = caldir + 'SUPER_DARK_'+sca_str+'.FITS' if dark else None

    # Instantiate a noise generator object
    ng_h2rg = ng.HXRGNoise(naxis1=det.xpix, naxis2=det.ypix, naxis3=naxis3, 
                 n_out=det.nout, nroh=nroh, nfoh=nfoh, nfoh_pix=nfoh_pix,
                 dark_file=dark_file, bias_file=bias_file,
                 wind_mode=det.wind_mode, x0=det.x0, y0=det.y0,
                 use_fftw=use_fftw, ncores=ncores, verbose=verbose)
 

    # Lists of each SCA and their corresponding noise info
    sca_arr = range(481,491)

    # These come from measured dark ramps acquired during ISIM CV3 at GSFC
    # Gain values (e/ADU). Everything else will be in measured ADU units
    gn_arr =  [2.07, 2.01, 2.16, 2.01, 1.83, 
               2.00, 2.42, 1.93, 2.30, 1.85]

    # Noise Values (ADU)
    ktc_arr = [18.5, 15.9, 15.2, 16.9, 20.0, 
               19.2, 16.1, 19.1, 19.0, 20.0]
    ron_arr  = [[4.8,4.9,5.0,5.3], [4.4,4.4,4.4,4.2], [4.8,4.0,4.1,4.0], [4.5,4.3,4.4,4.4],
                [4.2,4.0,4.5,5.4],
                [5.1,5.1,5.0,5.1], [4.6,4.3,4.5,4.2], [5.1,5.6,4.6,4.9], [4.4,4.5,4.3,4.0],
                [4.5,4.3,4.6,4.8]]
    # Pink Noise Values (ADU)
    cp_arr  = [ 2.0, 2.5, 1.9, 2.5, 2.1,
                2.5, 2.5, 3.2, 3.0, 2.5]
    up_arr  = [[0.9,0.9,0.9,0.9], [0.9,1.0,0.9,1.0], [0.8,0.9,0.8,0.8], [0.8,0.9,0.9,0.8],
               [1.0,1.3,1.0,1.1],
               [1.0,0.9,1.0,1.0], [0.9,0.9,1.1,1.0], [1.0,1.0,1.0,0.9], [1.1,1.1,0.8,0.9],
               [1.1,1.1,1.0,1.0]]
   
   
    # Offset Values (ADU)
    bias_avg_arr = [5900, 5400, 6400, 6150, 11650, 
                    7300, 7500, 6700, 7500, 11500]
    bias_sig_arr = [20.0, 20.0, 30.0, 11.0, 50.0, 
                    20.0, 20.0, 20.0, 20.0, 20.0]
    ch_off_arr   = [[1700, 530, -375, -2370], [-150, 570, -500, 350], [-530, 315, 460, -200],
                    [480, 775, 1040, -2280],  [560, 100, -440, -330],
                    [105, -29, 550, -735],    [315, 425, -110, -590],   [918, -270, 400, -1240],
                    [-100, 500, 300, -950],   [-35, -160, 125, -175]]
    f2f_corr_arr = [14.0, 13.8, 27.0, 14.0, 26.0,
                    14.7, 11.5, 18.4, 14.9, 14.8]
    f2f_ucorr_arr= [[18.4,11.1,10.8,9.5], [7.0,7.3,7.3,7.1], [6.9,7.3,7.3,7.5],
                    [6.9,7.3,6.5,6.7], [16.6,14.8,13.5,14.2],
                    [7.2,7.5,6.9,7.0], [7.2,7.6,7.5,7.4], [7.9,6.8,6.9,7.0],
                    [7.6,8.6,7.5,7.4], [13.3,14.3,14.1,15.1]]
    aco_a_arr    = [[770, 440, 890, 140], [800, 410, 840, 800], [210,680,730,885],
                    [595, 642, 634, 745], [-95,660,575,410],
                    [220, 600, 680, 665], [930,1112, 613, 150], [395, 340, 820, 304],
                    [112, 958, 690, 907], [495, 313, 392, 855]]
    ref_inst_arr = [1.0, 1.5, 1.0, 1.3, 1.0, 
                    1.0, 1.0, 1.0, 2.2, 1.0]


    # SCA Index
    ind = sca_arr.index(scaid)

    # Convert everything to e-
    gn = gn_arr[ind]
    # Noise Values
    ktc_noise= gn * ktc_arr[ind] * 1.15            # kTC noise in electrons
    rd_noise = gn * np.array(ron_arr[ind]) * 0.93  # White read noise per integration
    # Pink Noise
    c_pink   = gn * cp_arr[ind] * 1.6              # Correlated pink noise
    u_pink   = gn * np.array(up_arr[ind]) * 1.4    # Uncorrelated
    ref_rat  = 0.9  # Ratio of reference pixel noise to that of reg pixels

    # Offset Values
    bias_off_avg = gn * bias_avg_arr[ind] + 110  # On average, integrations start here in electrons
    bias_off_sig = gn * bias_sig_arr[ind]  # bias_off_avg has some variation. This is its std dev.
    bias_amp     = gn * 1.0     # A multiplicative factor to multiply bias_image. 1.0 for NIRCam.

    # Offset of each channel relative to bias_off_avg.
    ch_off = gn * np.array(ch_off_arr[ind]) + 110
    # Random frame-to-frame reference offsets due to PA reset
    ref_f2f_corr  = gn * f2f_corr_arr[ind] * 0.95
    ref_f2f_ucorr = gn * np.array(f2f_ucorr_arr[ind]) * 1.15 # per-amp
    # Relative offsets of altnernating columns
    aco_a = gn * np.array(aco_a_arr[ind])
    aco_b = -1 * aco_a
    #Reference Instability
    ref_inst = gn * ref_inst_arr[ind]

    # If only one output (window mode) then select first elements of each array
    if det.nout == 1:
        rd_noise = rd_noise[0]
        u_pink = u_pink[0]
        ch_off = ch_off[0]
        ref_f2f_ucorr = ref_f2f_ucorr[0]
        aco_a = aco_a[0]; aco_b = aco_b[0]

    # Run noise generator
    hdu = ng_h2rg.mknoise(None, gain=gn, rd_noise=rd_noise, c_pink=c_pink, u_pink=u_pink, 
            reference_pixel_noise_ratio=ref_rat, ktc_noise=ktc_noise,
            bias_off_avg=bias_off_avg, bias_off_sig=bias_off_sig, bias_amp=bias_amp,
            ch_off=ch_off, ref_f2f_corr=ref_f2f_corr, ref_f2f_ucorr=ref_f2f_ucorr, 
            aco_a=aco_a, aco_b=aco_b, ref_inst=ref_inst, out_ADU=out_ADU)

    hdu.header = nrc_header(det)#, header=hdu.header)
    hdu.header['UNITS'] = 'ADU' if out_ADU else 'e-'

    # Write the result to a FITS file
    if file_out is not None:
        now = datetime.datetime.now().isoformat()[:-7]
        hdu.header['DATE'] = datetime.datetime.now().isoformat()[:-7]
        if file_out.lower()[-5:] == '.fits':
            file_out = file_out[:-5]
        if file_out[-1:] == '_':
            file_out = file_out[:-1]

# 		file_now = now
# 		file_now = file_now.replace(':', 'h', 1)
# 		file_now = file_now.replace(':', 'm', 1)
# 		file_out = file_out + '_' + file_now + '.fits'
        file_out = file_out + '.fits'

        hdu.header['FILENAME'] = os.path.split(file_out)[1]
        hdu.writeto(file_out, clobber='True')

    return hdu

def slope_to_ramp(det, im_slope=None, out_ADU=False, file_out=None, 
                  filter=None, pupil=None, obs_time=None, targ_name=None,
                  DMS=True, dark=True, bias=True, det_noise=True,
                  return_results=True):
    """Convert slope image to simulated ramp
    
    For a given detector operations class and slope image, create a
    ramp integration using Poisson noise and detector noise. 

    Currently, this image simulator does NOT take into account:
    
        - QE variations across a pixel's surface
        - Intrapixel Capacitance (IPC)
        - Post-pixel Coupling (PPC) due to ADC "smearing"
        - Pixel non-linearity
        - Persistence/latent image
        - Optical distortions
        - Zodiacal background roll off for grism edges
        - Telescope jitter
        - Cosmic Rays

    Parameters
    ----------
    det : object
        Detector operations object.
    im_slope : ndarray
        Idealized slope image.

    out_ADU : bool
        If true, divide by gain and convert to 16-bit UINT.
    file_out : str
        Name (including directory) to save FITS file
    filter : str
        Name of filter element for header
    pupil : str
        Name of pupil element for header
    obs_time : datetime 
        Specifies when the observation was considered to be executed.
        If not specified, then it will choose the current time.
        This must be a datetime object:
            
            >>> datetime.datetime(2016, 5, 9, 11, 57, 5, 796686)
            
        This information is added to the header.
    targ_name : str
        Target name (optional)
    DMS : bool
        Package the data in the format used by DMS?
    dark : bool
        Include the dark current?
    bias : bool
        Include the bias frame?
    det_noise: bool
        Include detector noise components? If set to False, then only 
        perform Poisson noise. Darks and biases are also excluded.
    """
    
    if (det_noise==False) and (im_slope is None):
        _log.warning('No input slope image and det_noise=False. Nothing to return.')
        return

    #import ngNRC
    # MULTIACCUM ramp information
    ma  = det.multiaccum

    xpix = det.xpix
    ypix = det.ypix

    nd1     = ma.nd1
    nd2     = ma.nd2
    nf      = ma.nf
    ngroup  = ma.ngroup
    t_frame = det.time_frame

    # Number of total frames up the ramp (including drops)
    naxis3 = nd1 + ngroup*nf + (ngroup-1)*nd2

    if im_slope is not None:
        # Set reference pixels' slopes equal to 0
        w = det.ref_info
        if w[0] > 0: # lower
            im_slope[:w[0],:] = 0
        if w[1] > 0: # upper
            im_slope[-w[1]:,:] = 0
        if w[2] > 0: # left
            im_slope[:,:w[2]] = 0
        if w[3] > 0: # right
            im_slope[:,-w[3]:] = 0

        # Count accumulation for a single frame
        frame = im_slope * t_frame
        # Add Poisson noise at each frame step
        sh0, sh1 = im_slope.shape
        new_shape = (naxis3, sh0, sh1)
        ramp = np.random.poisson(lam=frame, size=new_shape)#.astype(np.float64)
        # Perform cumulative sum in place
        np.cumsum(ramp, axis=0, out=ramp)
        
        # Truncate anything above well level
        # TODO: Update per detector
        well_max = det.well_level
        ramp[ramp>well_max] = well_max
    else:
        ramp = 0

    if det_noise==False:
        hdu = fits.PrimaryHDU(ramp)
    else:
        # Create dark ramp with read noise and 1/f noise
        hdu = SCAnoise(det=det, dark=dark, bias=bias)
        # Add signal ramp to dark ramp
        if im_slope is not None:
            hdu.data += ramp.reshape(hdu.data.shape) 
    # Update header information
    hdu.header = det.make_header(filter, pupil, obs_time, targ_name=targ_name, DMS=DMS)
    data = hdu.data

    #### Add in IPC (TBI) ####

    # Get rid of any drops at the beginning (nd1)
    if nd1>0: data = data[nd1:,:,:]

    # Convert to ADU (16-bit UINT)
    if out_ADU:
        gain = det.gain
        data = data / gain
        data[data < 0] = 0
        data[data >= 2**16] = 2**16 - 1
        data = data.astype('uint16')
        hdu.header['UNITS'] = 'ADU'

    ## Save the first frame (so-called ZERO frame) for the zero frame extension
    zeroData = deepcopy(data[0,:,:])

    # Remove drops and average grouped data
    if nf>1 or nd2>0:
        # Trailing drop frames already excluded, so need to pull off last group of avg'ed frames
        data_end = data[-nf:,:,:].mean(axis=0) if nf>1 else data[-1:,:,:]
        data_end = data_end.reshape([1,ypix,xpix])

        # Only care about first (n-1) groups for now
        # Last group is handled separately
        data = data[:-nf,:,:]

        # Reshape for easy group manipulation
        data = data.reshape([-1,nf+nd2,ypix,xpix])

        # Trim off the dropped frames (nd2)
        if nd2>0: data = data[:,:nf,:,:]

        # Average the frames within groups
        # In reality, the 16-bit data is bit-shifted
        data = data.reshape([-1,ypix,xpix]) if nf==1 else data.mean(axis=1)

        # Add back the last group (already averaged)
        data = np.append(data,data_end,axis=0)

    hdu.data = data
    
    if file_out is not None:
        hdu.header['FILENAME'] = os.path.split(file_out)[1]
    
    if DMS == True:
        primHDU = fits.PrimaryHDU(header=hdu.header)
        primHDU.name = 'PRIMARY'
        sciHDU = fits.ImageHDU(data=hdu.data)
        sciHDU.name = 'SCI'
        sciHDU.header.comments['NAXIS1'] = 'length of first data axis (#columns)'
        sciHDU.header.comments['NAXIS2'] = 'length of second data axis (#rows)'
        if sciHDU.header['NAXIS'] > 2:
            sciHDU.header.comments['NAXIS3'] = 'length of third data axis (#groups/integration '
        if sciHDU.header['NAXIS'] > 3:
            sciHDU.header.comments['NAXIS4'] = 'length of fourth data axis (#integrations)  '
        sciHDU.header['BZERO'] = (32768, 'physical value for an array value of zero')
        sciHDU.header['BUNIT'] = ('DN', 'physical units of the data array values')
        
        zerHDU = fits.ImageHDU(data=zeroData)
        zerHDU.name = 'ZEROFRAME'
        zerHDU.header.comments['NAXIS1'] = 'length of first data axis (#columns)'
        zerHDU.header.comments['NAXIS2'] = 'length of second data axis (#rows)'
        
        outHDU = fits.HDUList([primHDU,sciHDU,zerHDU])
    else:
        outHDU = hdu
    
    if file_out is not None:
        outHDU.writeto(file_out, overwrite='True')
    
    # Only return outHDU if return_results=True
    if return_results: 
        return outHDU


def add_ipc(im, alpha_min=0.0065, alpha_max=None, kernel=None):
    """Convolve image with IPC kernel
    
    Given an image in electrons, apply IPC convolution.
    NIRCam average IPC values (alpha) reported 0.005 - 0.006.
    
    Parameters
    ==========
    im : ndarray
        Input image or array of images.
    alpha_min : float
        Minimum coupling coefficient between neighboring pixels.
        If alpha_max is None, then this is taken to be constant
        with respect to signal levels.
    alpha_max : float or None
        Maximum value of coupling coefficent. If specificed, then
        coupling between pixel pairs is assumed to vary depending
        on signal values. See Donlon et al., 2019, PASP 130.
    kernel : ndarry or None
        Option to directly specify the convolution kernel. 
    
    Examples
    ========
    Constant Kernel

        >>> im_ipc = add_ipc(im, alpha_min=0.0065)

    Constant Kernel (manual)

        >>> alpha = 0.0065
        >>> k = np.array([[0,alpha,0], [alpha,1-4*alpha,alpha], [0,alpha,0]])
        >>> im_ipc = add_ipc(im, kernel=k)

    Signal-dependent Kernel

        >>> im_ipc = add_ipc(im, alpha_min=0.0065, alpha_max=0.0145)

    """

    from astropy.convolution import convolve
    
    sh = im.shape
    ndim = len(sh)
    if ndim==2:
        im = im.reshape([1,sh[0],sh[1]])
        sh = im.shape
    
    # Pad images to have a 0 border
    im_pad = np.pad(im, ((0,0),(1,1),(1,1)), 'constant')
    
    # Check for custom kernel (overrides alpha values)
    if (kernel is not None) or (alpha_max is None):
        # Reshape to stack all image along horizontal axes
        im_reshape = im_pad.reshape([-1, im_pad.shape[-1]])
    
        if kernel is None:
            kernel = np.array([[0.0, alpha_min, 0.0],
                               [alpha_min, 1.-4*alpha_min, alpha_min],
                               [0.0, alpha_min, 0.0]])
    
        # Convolve IPC kernel with images
        im_ipc = convolve(im_reshape, kernel).reshape(im_pad.shape)
    
    # Exponential coupling strength
    # Equation 7 of Donlon et al. (2018)
    else:
        arrsqr = im_pad**2

        amin = alpha_min
        amax = alpha_max
        ascl = (amax-amin) / 2
        
        alpha_arr = []
        for ax in [1,2]:
            # Shift by -1
            diff = np.abs(im_pad - np.roll(im_pad, -1, axis=ax))
            sumsqr = arrsqr + np.roll(arrsqr, -1, axis=ax)
            
            imtemp = amin + ascl * np.exp(-diff/20000) + \
                     ascl * np.exp(-np.sqrt(sumsqr / 2) / 10000)
            alpha_arr.append(imtemp)
            # Take advantage of symmetries to shift in other direction
            alpha_arr.append(np.roll(imtemp, 1, axis=ax))
            
        alpha_arr = np.array(alpha_arr)

        # Flux remaining in parent pixel
        im_ipc = im_pad * (1 - alpha_arr.sum(axis=0))
        # Flux shifted to adjoining pixels
        for i, (shft, ax) in enumerate(zip([-1,+1,-1,+1], [1,1,2,2])):
            im_ipc += alpha_arr[i]*np.roll(im_pad, shft, axis=ax)
        del alpha_arr

    # Trim excess
    return im_ipc[:,1:-1,1:-1].squeeze()
    
    
def add_ppc(im, ppc_frac=0.002, nchans=4, 
    same_scan_direction=False, reverse_scan_direction=False,
    in_place=False):
    """ Add Post-Pixel Coupling (PPC)
    
    This effect is due to the incomplete settling of the analog
    signal when the ADC sample-and-hold pulse occurs. The measured
    signals for a given pixel will have a value that has not fully
    transitioned to the real analog signal. Mathematically, this
    can be treated in the same way as IPC, but with a different
    convolution kernel.
    
    Parameters
    ==========
    im : ndarray
        Image or array of images
    ppc_frac : float
        Fraction of signal contaminating next pixel in readout. 
    nchans : int
        Number of readout output channel amplifiers.
    same_scan_direction : bool
        Are all the output channels read in the same direction?
        By default fast-scan readout direction is ``[-->,<--,-->,<--]``
        If ``same_scan_direction``, then all ``-->``
    reverse_scan_direction : bool
        If ``reverse_scan_direction``, then ``[<--,-->,<--,-->]`` or all ``<--``
    in_place : bool
        Apply in place to input image.
    """

                       
    sh = im.shape
    ndim = len(sh)
    if ndim==2:
        im = im.reshape([1,sh[0],sh[1]])
        sh = im.shape

    nz, ny, nx = im.shape
    chsize = nx // nchans
    
    # Do each channel separately
    kernel = np.array([[0.0, 0.0, 0.0],
                       [0.0, 1.0-ppc_frac, ppc_frac],
                       [0.0, 0.0, 0.0]])
                       
    res = im if in_place else im.copy()
    for ch in np.arange(nchans):
        if same_scan_direction:
            k = kernel[:,::-1] if reverse_scan_direction else kernel
        elif np.mod(ch,2)==0:
            k = kernel[:,::-1] if reverse_scan_direction else kernel
        else:
            k = kernel if reverse_scan_direction else kernel[:,::-1]

        x1 = chsize*ch
        x2 = x1 + chsize
        res[:,:,x1:x2] = add_ipc(im[:,:,x1:x2], kernel=k)
    
                      
    return res.squeeze()


def ipc_deconvolve(im, kernel, kfft=None):
    """Simple IPC image deconvolution
    
    Given an image (or image cube), apply an IPC deconvolution kernel
    to obtain the intrinsic flux distribution. Should also work for 
    PPC kernels. This simply calculates the FFT of the image(s) and
    kernel, divides them, then applies an iFFT to determine the
    deconvolved image.
    
    If performing PPC deconvolution, make sure to perform channel-by-channel
    with the kernel in the appropriate scan direction. IPC is usually symmetric,
    so this restriction may not apply.
 
    Parameters
    ==========
    im : ndarray
        Image or array of images. 
    kernel : ndarry
        Deconvolution kernel.
    kfft : Complex ndarray
        Option to directy supply the kernel's FFT rather than
        calculating it within the function. The supplied ndarray
        should have shape (ny,nx) equal to the input `im`. Useful
        if calling ``ipc_deconvolve`` multiple times.
    """

    # bias the image to avoid negative pixel values in image
    min_im = np.min(im)
    im = im - min_im

    # FFT of input image
    imfft = np.fft.fft2(im)

    # FFT of kernel
    if kfft is None:
        ipc_big = pad_or_cut_to_size(kernel, (im.shape[-2],im.shape[-1]))
        kfft = np.fft.fft2(ipc_big)

    im_final = np.fft.fftshift(np.fft.ifft2(imfft/kfft).real, axes=(-2,-1)) + min_im

    return im_final

def ppc_deconvolve(im, kernel, kfft=None, nchans=4, 
    same_scan_direction=False, reverse_scan_direction=False):
    """PPC image deconvolution
    
    Given an image (or image cube), apply PPC deconvolution kernel
    to obtain the intrinsic flux distribution. 
    
    If performing PPC deconvolution, make sure to perform channel-by-channel
    with the kernel in the appropriate scan direction. IPC is usually symmetric,
    so this restriction may not apply.
 
    Parameters
    ==========
    im : ndarray
        Image or array of images. 
    kernel : ndarry
        Deconvolution kernel.
    kfft : Complex ndarray
        Option to directy supply the kernel's FFT rather than
        calculating it within the function. The supplied ndarray
        should have shape (ny,nx) equal to the input `im`. Useful
        if calling ``ipc_deconvolve`` multiple times.
    """


    # Image cube shape
    sh = im.shape
    ndim = len(sh)
    if ndim==2:
        ny, nx = sh
        nz = 1
    else:
        nz, ny, nx = sh
    chsize = int(nx / nchans)
    im = im.reshape([nz,ny,nchans,-1])

    # FFT of kernel
    if kfft is None:
        k_big = pad_or_cut_to_size(kernel, (ny,chsize))
        kfft = np.fft.fft2(k_big)

    # Channel-by-channel deconvolution
    for ch in np.arange(nchans):
        sub = im[:,:,ch,:]
        if same_scan_direction:
            flip = True if reverse_scan_direction else False
        elif np.mod(ch,2)==0:
            flip = True if reverse_scan_direction else False
        else:
            flip = False if reverse_scan_direction else True

        if flip: 
            sub = sub[:,:,:,::-1]

        sub = ipc_deconvolve(sub, kernel, kfft=kfft)
        if flip: 
            sub = sub[:,:,:,::-1]
        im[:,:,ch,:] = sub

    im = im.reshape(sh)

    return im


def get_ipc_kernel(imdark, tint, boxsize=5, nchans=4, bg_remove=True,
                   hotcut=[5000,50000], calc_ppc=False,
                   same_scan_direction=False, reverse_scan_direction=False):
    """ Derive IPC/PPC Convolution Kernels
    
    Find the IPC and PPC kernels used to convolve detector pixel data.
    Finds all hot pixels within hotcut parameters and measures the
    average relative flux within adjacent pixels.

    Parameters
    ==========

    Keyword Parameters
    ==================
    boxsize : int
        Size of the box. Should be odd, but if even, then adds +1.
    bg_remove : bool
        Remove the average dark current values for each hot pixel cut-out.
        Only works if boxsize>3.
    hotcut : array-like
        Min and max values of hot pixels (above bg and bias) to cosider.
    calc_ppc : bool
        Calculate and return post-pixel coupling?
    same_scan_direction : bool
        Are all the output channels read in the same direction?
        By default fast-scan readout direction is ``[-->,<--,-->,<--]``
        If ``same_scan_direction``, then all ``-->``
    reverse_scan_direction : bool
        If ``reverse_scan_direction``, then ``[<--,-->,<--,-->]`` or all ``<--``

    """
    
    ny, nx = imdark.shape
    chsize = int(ny / nchans)

    imtemp = imdark * tint

    boxhalf = int(boxsize/2)
    boxsize = int(2*boxhalf + 1)
    distmin = np.ceil(np.sqrt(2.0) * boxhalf)

    # Get rid of pixels around border
    pixmask = ((imtemp>hotcut[0]) & (imtemp<hotcut[1]))
    pixmask[0:4+boxhalf, :] = False
    pixmask[-4-boxhalf:, :] = False
    pixmask[:, 0:4+boxhalf] = False
    pixmask[:, -4-boxhalf:] = False

    # Ignore borders between amplifiers
    for ch in range(1, nchans):
        x1 = ch*chsize - boxhalf
        x2 = x1 + 2*boxhalf
        pixmask[:, x1:x2] = False
    indy, indx = np.where(pixmask)
    nhot = len(indy)
    if nhot < 2:
        print("No hot pixels found!")
        return None

    # Only want isolated pixels
    # Get distances for every pixel
    # If too close, then set equal to 0
    for i in range(nhot):
        d = np.sqrt((indx-indx[i])**2 + (indy-indy[i])**2)
        ind_close = np.where((d>0) & (d<distmin))[0]
        if len(ind_close)>0: pixmask[indy[i], indx[i]] = 0
    indy, indx = np.where(pixmask)
    nhot = len(indy)
    if nhot < 2:
        print("No hot pixels found!")
        return None

    # Stack all hot pixels in a cube
    hot_all = []
    for iy, ix in zip(indy, indx):
        x1, y1 = np.array([ix,iy]) - boxhalf
        x2, y2 = np.array([x1,y1]) + boxsize
        sub = imtemp[y1:y2, x1:x2]

        # Flip channels along x-axis for PPC
        if calc_ppc:
            # Check if an even or odd channel (index 0)
            for ch in np.arange(0,nchans,2):
                even = True if (ix > ch*chsize) and (ix < (ch+1)*chsize-1) else False
        
            if same_scan_direction:
                flip = True if reverse_scan_direction else False
            elif even:
                flip = True if reverse_scan_direction else False
            else:
                flip = False if reverse_scan_direction else True

            if flip: sub = sub[:,::-1]

        hot_all.append(sub)
    hot_all = np.array(hot_all)

    # Remove average dark current values
    if boxsize>3 and bg_remove==True:
        for im in hot_all:
            im -= np.median([im[0,:], im[:,0], im[-1,:], im[:,-1]])

    # Normalize by sum in 3x3 region
    norm_all = hot_all.copy()
    for im in norm_all:
        im /= im[boxhalf-1:boxhalf+2, boxhalf-1:boxhalf+2].sum()

    # Take average of normalized stack
    ipc_im_avg = np.median(norm_all, axis=0)
#     ipc_im_sig = robust.medabsdev(norm_all, axis=0)

    corner_val = (ipc_im_avg[boxhalf-1,boxhalf-1] + 
                 ipc_im_avg[boxhalf+1,boxhalf+1] + 
                 ipc_im_avg[boxhalf+1,boxhalf-1] + 
                 ipc_im_avg[boxhalf-1,boxhalf+1]) / 4
    if corner_val<0: corner_val = 0

    # Determine post-pixel coupling value?
    if calc_ppc:
        ipc_val = (ipc_im_avg[boxhalf-1,boxhalf] + \
                  ipc_im_avg[boxhalf,boxhalf-1] + \
                  ipc_im_avg[boxhalf+1,boxhalf]) / 3
        if ipc_val<0: ipc_val = 0
            
        ppc_val = ipc_im_avg[boxhalf,boxhalf+1] - ipc_val
        if ppc_val<0: ppc_val = 0

        k_ipc = np.array([[corner_val, ipc_val, corner_val],
                         [ipc_val, 1-4*ipc_val, ipc_val],
                         [corner_val, ipc_val, corner_val]])
        k_ppc = np.zeros([3,3])
        k_ppc[1,1] = 1 - ppc_val
        k_ppc[1,2] = ppc_val
        
        return (k_ipc, k_ppc)
        
    # Just determine IPC
    else:
        ipc_val = (ipc_im_avg[boxhalf-1,boxhalf] + 
                  ipc_im_avg[boxhalf,boxhalf-1] + 
                  ipc_im_avg[boxhalf,boxhalf+1] + 
                  ipc_im_avg[boxhalf+1,boxhalf]) / 4
        if ipc_val<0: ipc_val = 0

        kernel = np.array([[corner_val, ipc_val, corner_val],
                           [ipc_val, 1-4*ipc_val, ipc_val],
                           [corner_val, ipc_val, corner_val]])
        
        return kernel


# npix = 20
# arr = np.random.rand(npix) * 4000
# arr[1] = 0
# #arr[-1] = 2**16-1
# 
# pixel_time = 10. # usec
# 
# darr = np.roll(arr,-1) - arr
# tarr = np.arange(100.) / pixel_time
# tau = 1.5
# vt = darr.reshape([-1,1])*(1-np.exp(-tarr.reshape([1,-1])/tau))
# vt = vt.ravel()
# 
# arr_full = arr.repeat(len(tarr))
# arr_new = np.roll(arr_full + vt, len(tarr))
# 
# tfull = np.arange(len(arr_new)) * pixel_time / len(tarr)
# 
# # Pixel sample times
# t_sample = np.arange(npix)*pixel_time + 6.
# v_sample = np.interp(t_sample, tfull, arr_new)
# 
# fig, ax = plt.subplots(1,1, figsize=(12,5))
# ax.plot(tfull, arr_full)#[0:-20])
# ax.plot(tfull, arr_new)
# ax.xaxis.get_major_locator().set_params(nbins=9, steps=[1, 2, 5, 10])
# 
# fig.tight_layout()
# 
# from astropy.convolution import convolve
# alpha = 0.018
# kernel = np.array([0.0, 1-alpha, alpha])
# arr_ppc = convolve(arr, kernel)
# 
# 
# arr[1] = 0
# arr[-1] = 2**16-1
# # Convert to usec timestep
# arr = arr.repeat(10)
# 
# from scipy.ndimage import gaussian_filter1d
# res = gaussian_filter1d(arr, 1.5)
# 
# plt.clf()
# plt.plot(arr[0:-20])
# plt.plot(res[0:-20])
# 
# 
# res = arr.copy()
# res = res.reshape([npix,-1])
# res[:, 0:3] = np.nan
# res[:, -3:] = np.nan
# res = res.ravel()
# 
# im_mask = np.isnan(res)
# x = mask_helper() # Returns the nonzero (True) indices of a mask
# res[im_mask]= np.interp(x(im_mask), x(~im_mask), res[~im_mask])
# 
# plt.clf()
# plt.plot(arr[0:-20])
# plt.plot(res[0:-20])
# 
# 
# from scipy.signal import butter, filtfilt
# b, a = butter(3, 0.2, btype='lowpass', analog=False)
# res = filtfilt(b, a, arr)
# 
# from scipy.signal import savgol_filter
# winsize = 5
# res2 = savgol_filter(arr, winsize, 3)
# 
# plt.clf()
# plt.plot(arr)
# plt.plot(res)
# #plt.plot(res2)
# 
