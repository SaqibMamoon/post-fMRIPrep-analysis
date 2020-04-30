"""
Analysis workflows
"""

from nipype.pipeline import engine as pe
from nipype.algorithms.modelgen import SpecifyModel
from nipype.interfaces import fsl, utility as niu, io as nio
from nipype.workflows.fmri.fsl.preprocess import create_susan_smooth
from niworkflows.interfaces.bids import DerivativesDataSink as BIDSDerivatives
from interfaces import PtoZ


DATA_ITEMS = ['bold', 'mask', 'events', 'regressors', 'tr']


class DerivativesDataSink(BIDSDerivatives):
    out_path_base = 'FSLAnalysis'


class GroupDerivativesDataSink(BIDSDerivatives):
    out_path_base = 'grp_all'


def first_level_wf(in_files, output_dir, fwhm=6.0, name='wf_1st_level'):
    workflow = pe.Workflow(name=name)
    datasource = pe.Node(niu.Function(function=_dict_ds, output_names=DATA_ITEMS),
                         name='datasource')
    datasource.inputs.in_dict = in_files
    datasource.iterables = ('sub', sorted(in_files.keys()))

    # Extract motion parameters from regressors file
    runinfo = pe.Node(niu.Function(
        input_names=['in_file', 'events_file', 'regressors_file', 'regressors_names'],
        function=_bids2nipypeinfo, output_names=['info', 'realign_file']),
        name='runinfo')

    # Set the column names to be used from the confounds file
    runinfo.inputs.regressors_names = ['dvars', 'framewise_displacement'] + \
        ['a_comp_cor_%02d' % i for i in range(6)] + ['cosine%02d' % i for i in range(4)]

    # SUSAN smoothing
    susan = create_susan_smooth()
    susan.inputs.inputnode.fwhm = fwhm

    l1_spec = pe.Node(SpecifyModel(
        parameter_source='FSL',
        input_units='secs',
        high_pass_filter_cutoff=100
    ), name='l1_spec')

    # l1_model creates a first-level model design
    l1_model = pe.Node(fsl.Level1Design(
        bases={'dgamma': {'derivs': True}},
        model_serial_correlations=True,
        #ENTER YOUR OWN CONTRAST HERE
        contrasts=[],
        # orthogonalization=orthogonality,
    ), name='l1_model')

    # feat_spec generates an fsf model specification file
    feat_spec = pe.Node(fsl.FEATModel(), name='feat_spec')
    # feat_fit actually runs FEAT
    feat_fit = pe.Node(fsl.FEAT(), name='feat_fit', mem_gb=12)

    feat_select = pe.Node(nio.SelectFiles({
        'cope': 'stats/cope1.nii.gz',
        'pe': 'stats/pe[0-9][0-9].nii.gz',
        'tstat': 'stats/tstat1.nii.gz',
        'varcope': 'stats/varcope1.nii.gz',
        'zstat': 'stats/zstat1.nii.gz',
    }), name='feat_select')

    ds_cope = pe.Node(DerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='cope',
        desc='intask'), name='ds_cope', run_without_submitting=True)

    ds_varcope = pe.Node(DerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='varcope',
        desc='intask'), name='ds_varcope', run_without_submitting=True)

    ds_zstat = pe.Node(DerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='zstat',
        desc='intask'), name='ds_zstat', run_without_submitting=True)

    ds_tstat = pe.Node(DerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='tstat',
        desc='intask'), name='ds_tstat', run_without_submitting=True)

    workflow.connect([
        (datasource, susan, [('bold', 'inputnode.in_files'),
                             ('mask', 'inputnode.mask_file')]),
        (datasource, runinfo, [
            ('events', 'events_file'),
            ('regressors', 'regressors_file')]),
        (susan, l1_spec, [('outputnode.smoothed_files', 'functional_runs')]),
        (datasource, l1_spec, [('tr', 'time_repetition')]),
        (datasource, l1_model, [('tr', 'interscan_interval')]),
        (datasource, ds_cope, [('bold', 'source_file')]),
        (datasource, ds_varcope, [('bold', 'source_file')]),
        (datasource, ds_zstat, [('bold', 'source_file')]),
        (datasource, ds_tstat, [('bold', 'source_file')]),
        (susan, runinfo, [('outputnode.smoothed_files', 'in_file')]),
        (runinfo, l1_spec, [
            ('info', 'subject_info'),
            ('realign_file', 'realignment_parameters')]),
        (l1_spec, l1_model, [('session_info', 'session_info')]),
        (l1_model, feat_spec, [
            ('fsf_files', 'fsf_file'),
            ('ev_files', 'ev_files')]),
        (l1_model, feat_fit, [('fsf_files', 'fsf_file')]),
        (feat_fit, feat_select, [('feat_dir', 'base_directory')]),
        (feat_select, ds_cope, [('cope', 'in_file')]),
        (feat_select, ds_varcope, [('varcope', 'in_file')]),
        (feat_select, ds_zstat, [('zstat', 'in_file')]),
        (feat_select, ds_tstat, [('tstat', 'in_file')]),
    ])
    return workflow


def second_level_wf(output_dir, bids_ref, name='wf_2nd_level'):
    workflow = pe.Workflow(name=name)

    inputnode = pe.Node(niu.IdentityInterface(
        fields=['group_mask', 'in_copes', 'in_varcopes']),
        name='inputnode')

    outputnode = pe.Node(niu.IdentityInterface(
        fields=['zstats_raw', 'zstats_fwe', 'zstats_clust',
                'clust_index_file', 'clust_localmax_txt_file']),
        name='outputnode')

    # Configure FSL 2nd level analysis
    l2_model = pe.Node(fsl.L2Model(), name='l2_model')
    flameo_ols = pe.Node(fsl.FLAMEO(run_mode='ols'), name='flameo_ols')

    merge_copes = pe.Node(fsl.Merge(dimension='t'), name='merge_copes')
    merge_varcopes = pe.Node(fsl.Merge(dimension='t'), name='merge_varcopes')

    # Thresholding - FDR ################################################
    # Calculate pvalues with ztop
    fdr_ztop = pe.Node(fsl.ImageMaths(op_string='-ztop', suffix='_pval'),
                       name='fdr_ztop')
    # Find FDR threshold: fdr -i zstat1_pval -m <group_mask> -q 0.05
    # fdr_th = <write Nipype interface for fdr>
    # Apply threshold:
    # fslmaths zstat1_pval -mul -1 -add 1 -thr <fdr_th> -mas <group_mask> \
    #     zstat1_thresh_vox_fdr_pstat1

    # Thresholding - FWE ################################################
    # smoothest -r %s -d %i -m %s
    smoothness = pe.Node(fsl.SmoothEstimate(), name='smoothness')
    # ptoz 0.025 -g %f
    # p = 0.05 / 2 for 2-tailed test
    fwe_ptoz = pe.Node(PtoZ(pvalue=0.025), name='fwe_ptoz')
    # fslmaths %s -uthr %s -thr %s nonsignificant
    # fslmaths %s -sub nonsignificant zstat1_thresh
    fwe_nonsig0 = pe.Node(fsl.Threshold(direction='above'), name='fwe_nonsig0')
    fwe_nonsig1 = pe.Node(fsl.Threshold(direction='below'), name='fwe_nonsig1')
    fwe_thresh = pe.Node(fsl.BinaryMaths(operation='sub'), name='fwe_thresh')

    # Thresholding - Cluster ############################################
    # cluster -i %s -c %s -t 3.2 -p 0.025 -d %s --volume=%s  \
    #     --othresh=thresh_cluster_fwe_zstat1 --connectivity=26 --mm
    cluster_kwargs = {
        'connectivity': 26,
        'threshold': 3.2,
        'pthreshold': 0.025,
        'out_threshold_file': True,
        'out_index_file': True,
        'out_localmax_txt_file': True
    }
    cluster_pos = pe.Node(fsl.Cluster(
            **cluster_kwargs),
        name='cluster_pos')
    cluster_neg = pe.Node(fsl.Cluster(
            **cluster_kwargs),
        name='cluster_neg')
    zstat_inv = pe.Node(fsl.BinaryMaths(operation='mul', operand_value=-1),
                        name='zstat_inv')
    cluster_inv = pe.Node(fsl.BinaryMaths(operation='mul', operand_value=-1),
                          name='cluster_inv')
    cluster_all = pe.Node(fsl.BinaryMaths(operation='add'), name='cluster_all')

    ds_zraw = pe.Node(GroupDerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='zstat', sub='all'),
        name='ds_zraw', run_without_submitting=True)
    ds_zraw.inputs.source_file = bids_ref

    ds_zfwe = pe.Node(GroupDerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='zstat',
        desc='fwe', sub='all'), name='ds_zfwe', run_without_submitting=True)
    ds_zfwe.inputs.source_file = bids_ref

    ds_zclust = pe.Node(GroupDerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='zstat',
        desc='clust', sub='all'), name='ds_zclust', run_without_submitting=True)
    ds_zclust.inputs.source_file = bids_ref

    ds_clustidx_pos = pe.Node(GroupDerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='pclusterindex', sub='all'),
        name='ds_clustidx_pos', run_without_submitting=True)
    ds_clustidx_pos.inputs.source_file = bids_ref

    ds_clustlmax_pos = pe.Node(GroupDerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='plocalmax',
        desc='intask', sub='all'), name='ds_clustlmax_pos', run_without_submitting=True)
    ds_clustlmax_pos.inputs.source_file = bids_ref

    ds_clustidx_neg = pe.Node(GroupDerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='nclusterindex', sub='all'),
        name='ds_clustidx_neg', run_without_submitting=True)
    ds_clustidx_neg.inputs.source_file = bids_ref

    ds_clustlmax_neg = pe.Node(GroupDerivativesDataSink(
        base_directory=str(output_dir), keep_dtype=False, suffix='nlocalmax',
        desc='intask', sub='all'), name='ds_clustlmax_neg', run_without_submitting=True)
    ds_clustlmax_neg.inputs.source_file = bids_ref

    workflow.connect([
        (inputnode, l2_model, [(('in_copes', _len), 'num_copes')]),
        (inputnode, flameo_ols, [('group_mask', 'mask_file')]),
        (inputnode, smoothness, [('group_mask', 'mask_file'),
                                 (('in_copes', _dof), 'dof')]),
        (inputnode, merge_copes, [('in_copes', 'in_files')]),
        (inputnode, merge_varcopes, [('in_varcopes', 'in_files')]),

        (l2_model, flameo_ols, [('design_mat', 'design_file'),
                                ('design_con', 't_con_file'),
                                ('design_grp', 'cov_split_file')]),
        (merge_copes, flameo_ols, [('merged_file', 'cope_file')]),
        (merge_varcopes, flameo_ols, [('merged_file', 'var_cope_file')]),
        (flameo_ols, smoothness, [('res4d', 'residual_fit_file')]),

        (flameo_ols, fwe_nonsig0, [('zstats', 'in_file')]),
        (fwe_nonsig0, fwe_nonsig1, [('out_file', 'in_file')]),
        (smoothness, fwe_ptoz, [('resels', 'resels')]),
        (fwe_ptoz, fwe_nonsig0, [('zstat', 'thresh')]),
        (fwe_ptoz, fwe_nonsig1, [(('zstat', _neg), 'thresh')]),
        (flameo_ols, fwe_thresh, [('zstats', 'in_file')]),
        (fwe_nonsig1, fwe_thresh, [('out_file', 'operand_file')]),

        (flameo_ols, cluster_pos, [('zstats', 'in_file')]),
        (merge_copes, cluster_pos, [('merged_file', 'cope_file')]),
        (smoothness, cluster_pos, [('volume', 'volume'),
                                   ('dlh', 'dlh')]),
        (flameo_ols, zstat_inv, [('zstats', 'in_file')]),
        (zstat_inv, cluster_neg, [('out_file', 'in_file')]),
        (cluster_neg, cluster_inv, [('threshold_file', 'in_file')]),
        (merge_copes, cluster_neg, [('merged_file', 'cope_file')]),
        (smoothness, cluster_neg, [('volume', 'volume'),
                                   ('dlh', 'dlh')]),
        (cluster_pos, cluster_all, [('threshold_file', 'in_file')]),
        (cluster_inv, cluster_all, [('out_file', 'operand_file')]),

        (flameo_ols, ds_zraw, [('zstats', 'in_file')]),
        (fwe_thresh, ds_zfwe, [('out_file', 'in_file')]),
        (cluster_all, ds_zclust, [('out_file', 'in_file')]),
        (cluster_pos, ds_clustidx_pos, [('index_file', 'in_file')]),
        (cluster_pos, ds_clustlmax_pos, [('localmax_txt_file', 'in_file')]),
        (cluster_neg, ds_clustidx_neg, [('index_file', 'in_file')]),
        (cluster_neg, ds_clustlmax_neg, [('localmax_txt_file', 'in_file')]),
    ])
    return workflow


def _bids2nipypeinfo(in_file, events_file, regressors_file,
                     regressors_names=None,
                     motion_columns=None,
                     decimals=3, amplitude=1.0):
    from pathlib import Path
    import numpy as np
    import pandas as pd
    from nipype.interfaces.base.support import Bunch

    # Process the events file
    events = pd.read_csv(events_file, sep=r'\s+')

    bunch_fields = ['onsets', 'durations', 'amplitudes']

    if not motion_columns:
        from itertools import product
        motion_columns = ['_'.join(v) for v in product(('trans', 'rot'), 'xyz')]

    out_motion = Path('motion.par').resolve()

    regress_data = pd.read_csv(regressors_file, sep=r'\s+')
    np.savetxt(out_motion, regress_data[motion_columns].values, '%g')
    if regressors_names is None:
        regressors_names = sorted(set(regress_data.columns) - set(motion_columns))

    if regressors_names:
        bunch_fields += ['regressor_names']
        bunch_fields += ['regressors']

    runinfo = Bunch(
        scans=in_file,
        conditions=list(set(events.trial_type.values)),
        **{k: [] for k in bunch_fields})

    for condition in runinfo.conditions:
        event = events[events.trial_type.str.match(condition)]

        runinfo.onsets.append(np.round(event.onset.values, 3).tolist())
        runinfo.durations.append(np.round(event.duration.values, 3).tolist())
        if 'amplitudes' in events.columns:
            runinfo.amplitudes.append(np.round(event.amplitudes.values, 3).tolist())
        else:
            runinfo.amplitudes.append([amplitude] * len(event))

    if 'regressor_names' in bunch_fields:
        runinfo.regressor_names = regressors_names
        try:
            runinfo.regressors = regress_data[regressors_names]
        except KeyError:
            regressors_names = list(set(regressors_names).intersection(
                                    set(regress_data.columns)))
            runinfo.regressors = regress_data[regressors_names]
        runinfo.regressors = runinfo.regressors.fillna(0.0).values.T.tolist()

    return [runinfo], str(out_motion)


def _get_tr(in_dict):
    return in_dict.get('RepetitionTime')


def _len(inlist):
    return len(inlist)


def _dof(inlist):
    return len(inlist) - 1


def _neg(val):
    return -val


def _dict_ds(in_dict, sub, order=['bold', 'mask', 'events', 'regressors', 'tr']):
    return tuple([in_dict[sub][k] for k in order])
