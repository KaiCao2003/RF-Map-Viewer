"""Faithful Python translation of MasterFile_EB_VisualStimuli.m.

Low-level MATLAB helper replacements are imported from local Utils modules.
"""

import math
import os
import re
import csv
import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from scipy.io import loadmat, savemat
from scipy.optimize import least_squares

from Utils.load_files import load_binary as _load_binary


def MasterFile_EB_VisualStimuli(choice=None):
    cfg = VisStimConfig()
    choiceStr = "do_choice%02d" % 3
    if choiceStr in globals() and callable(globals()[choiceStr]):
        D = globals()[choiceStr]()
        print(D)
    else:
        raise RuntimeError("Choice %s does not correspond to a defined function." % choice)

def do_choice14():
    cfg = VisStimConfig()
    session = cfg.session
    settingsFile = VisStimSettingsFile()

    guo = True
    load_data_manually = False
    manual_curation = False

    probe_letter = "A"
    if "Probe" in session:
        probe_letter = re.search(r"Probe([A-Za-z])", session).group(1)

    if load_data_manually:
        ks_path = input("Please select kilosort folder: ")
        concat_f = input("Please select concat raw file: ")
        nchan = 384
    else:
        ks_path = VisStimProbePath(probe_letter, "kilosort")
        concat_f = VisStimInputFile("probe_concat_raw")
        nchan = 384

    lf = VisStimMatFile("New_Unit_Labels", session + ".mat")
    EnsureParentDir(lf)

    if not os.path.isfile(lf):
        ksl_f = VisStimInputFile("cluster_KSLabel")
        cg_f = VisStimInputFile("cluster_group")

        ksl = read_tsv_table(ksl_f)
        cg = read_tsv_table(cg_f)
        new_labels = copy_table(ksl)

        if manual_curation:
            edited_idx = []
            missing_idx = []
            ksl_ids = np.asarray(new_labels["cluster_id"])
            cg_ids = np.asarray(cg["cluster_id"])
            for c in range(len(cg_ids)):
                hit = np.where(ksl_ids == cg_ids[c])[0]
                if len(hit) == 0:
                    missing_idx.append(c)
                else:
                    edited_idx.append(int(hit[0]))

            for out_idx, cg_idx in zip(edited_idx, [i for i in range(len(cg_ids)) if i not in missing_idx]):
                new_labels["KSLabel"][out_idx] = cg["group"][cg_idx]

            if len(missing_idx) > 0:
                first_new_id = cg_ids[missing_idx[0]]
                start_hits = np.where(ksl_ids < first_new_id)[0]
                start = int(start_hits[-1] + 1)
                for key in new_labels:
                    move_to_end = list(new_labels[key][start:])
                    inserts = [cg[key][i] for i in missing_idx]
                    new_labels[key] = list(new_labels[key][:start]) + inserts + move_to_end

        save_mat_file(lf, new_labels=new_labels)

    DepthSort_meanWaveForms(ks_path, concat_f, session, settingsFile, 384, guo, nchan, 1)

    wf_p = VisStimMatFile("SpikeWaveforms", "mean_wfs", session + ".mat")
    loaded = load_mat_file(wf_p)
    m = loaded["meanWaveforms"]

    plt.figure()
    sel = range(25)
    pos = 1
    for i in sel:
        plt.subplot(5, 5, pos)
        ch_i = int(field(m, "ptp_chan_idx")[i]) - 1
        uid = str(int(field(m, "unitIds")[i]))
        chan = str(int(field(m, "chanmap")[ch_i]))
        plt.plot(field(m, "timepts"), field(m, "data")[ch_i, :, i])
        plt.title("uid: " + uid + ", ch: " + chan)
        pos += 1

    return "Finished do_choice14"

def do_choice02():
    print("Choice 02: Sync full-field grating data and show summary figures")

    cfg = VisStimConfig()
    rn = cfg.gratingSpreadsheetRow
    mode_sel = "mean"
    use_events = True
    sync2adc = False
    guo = True
    load_data_manually = False

    tb = read_spreadsheet_table(VisStimSpreadsheetFile("Info_Grating.xlsx"))
    row = tb[rn - 1]
    session_folder = row["session_folder"]
    session = row["Session"]
    probe_letter = "A"
    if "Probe" in session:
        probe_letter = re.search(r"Probe([A-Za-z])", session).group(1)

    if load_data_manually:
        ks_path = input("Please select kilosort folder: ")
        stim_path = input("Please select Psychtoolbox file: ")
        settingsFile = input("Please select Probe settings file: ")
        fn = session
    else:
        ks_path = VisStimProbePath(probe_letter, "kilosort")
        stim_path = VisStimStimFile("grating")
        settingsFile = VisStimSettingsFile(session_folder)
        fn = session

    prec_files = row["prec_files"]
    protocol = row["Protocol"]

    fp = VisStimMatFile("Grating_data", "Sync_Pulses", fn + ".mat")
    EnsureParentDir(fp)

    if os.path.isfile(fp):
        loaded = load_mat_file(fp)
        Vq = loaded["Vq"]
        trials = load_mat_file(stim_path)["trials"]
    else:
        trials, Vq = Sync_Signals(session_folder, ks_path, stim_path, prec_files, settingsFile, protocol, None, use_events, sync2adc, probe_letter)
        save_mat_file(fp, Vq=Vq)

    GratingAnalysis_EB(ks_path, fn, trials, Vq, mode_sel, settingsFile, sync2adc, guo)
    return "Finished do_choice02"

def do_choice03():
    print("Choice 03: Sync RF mapping data and show summary figures")

    cfg = VisStimConfig()
    rn = cfg.rfSpreadsheetRow
    mode_sel = "mean"
    nbins = 25
    save_figs = True
    save_gauss = True
    use_events = True
    sync2adc = False
    guo = True
    load_data_manually = False

    tb = read_spreadsheet_table(VisStimSpreadsheetFile("Info_RFmapping.xlsx"))
    row = tb[rn - 1]
    session_folder = cfg.sessionFolder
    gdp = VisStimMatFile("RF_maps", "Gaussian_Data")
    EnsureDir(gdp)
    gdf = fullfile(gdp, VisStimFileStem(session_folder) + ".mat")
    gdi = gdf.replace(".mat", "_idx.mat")
    session = cfg.session
    probe_letter = "A"
    if "Probe" in session:
        probe_letter = re.search(r"Probe([A-Za-z])", session).group(1)

    if load_data_manually:
        ks_path = input("Please select kilosort folder: ")
        stim_path = input("Please select Psychtoolbox file: ")
        settingsFile = input("Please select Probe settings file: ")
    else:
        stim_path = VisStimStimFile("rfmapping")
        ks_path = VisStimProbePath(probe_letter, "kilosort")
        settingsFile = VisStimSettingsFile(session_folder)

    fp = VisStimMatFile("RF_maps", "Sync_Pulses", session + ".mat")
    EnsureParentDir(fp)
    prec_files = row["prec_files"]
    protocol = row["Protocol"]

    if os.path.isfile(fp):
        loaded = load_mat_file(fp)
        Vq = loaded["Vq"]
        trials = load_mat_file(stim_path)["trials"]
    else:
        trials, Vq = Sync_Signals(session_folder, ks_path, stim_path, prec_files, settingsFile, protocol, None, use_events, sync2adc, probe_letter)
        save_mat_file(fp, Vq=Vq)

    if os.path.isfile(gdf):
        gauss_2d_data = load_mat_file(gdf)["gauss_2d_data"]
    else:
        gauss_2d_data = []

    RFmapping_EB(ks_path, session, trials, Vq, mode_sel, nbins, save_figs, save_gauss, gauss_2d_data, gdi, settingsFile, sync2adc, guo)
    return "Finished do_choice03"

def Sync_Signals(cf, ks_path, stim_path, prec_files, settingsFile, protocol, stim_name, use_events, sync2adc, probe_letter):
    plt.close("all")

    sr = 30000
    sr_adc = 30300.5
    nchan_probe = 385
    nchan_adc = 12

    stim = load_mat_file(stim_path)
    trials = stim["trials"]
    stimInfo = trials

    d0 = LoadBinary(VisStimRawFile(cf, "adc_continuous"), frequency=sr_adc, nChannels=12, channels=1)
    d0_dur = len(d0) / sr_adc

    snd0_cont = readNPY(VisStimRawFile(cf, "adc_continuous_sample_numbers")).astype(float)

    tsd0 = readNPY(VisStimRawFile(cf, "adc_ttl_timestamps"))
    snd0 = readNPY(VisStimRawFile(cf, "adc_ttl_sample_numbers")).astype(float)
    snd0 = snd0 - snd0_cont[0]
    snd0t = snd0 / sr_adc

    if protocol not in ["RFmapping360", "Grating360"]:
        d1_chan = 2
    else:
        d1_chan = 4

    d1 = LoadBinary(VisStimRawFile(cf, "adc_continuous"), frequency=sr_adc, nChannels=12, channels=d1_chan)
    d1_dur = len(d1) / sr_adc

    if protocol != "RFmapping360":
        d385 = LoadBinary(VisStimRawFile(cf, "probe_continuous"), frequency=sr, nChannels=nchan_probe, channels=385)
        d385_dur = len(d385) / sr

    snd385_cont = readNPY(VisStimRawFile(cf, "probe_continuous_sample_numbers")).astype(float)

    tsd385 = readNPY(VisStimRawFile(cf, "probe_ttl_timestamps"))
    snd385 = readNPY(VisStimRawFile(cf, "probe_ttl_sample_numbers")).astype(float)
    snd385 = snd385 - snd385_cont[0]
    snd385t = snd385 / sr

    prec_files = VisStimText(prec_files)
    pfiles = prec_files.split(",")
    nfiles = len(pfiles)
    if prec_files == "" or prec_files.lower() == "nan":
        nfiles = 0

    d0_dur_ar = np.zeros(nfiles)
    d0_ns_ar = np.zeros(nfiles)
    d385_dur_ar = np.zeros(nfiles)
    bytesPerSample = 2

    for ff in range(nfiles):
        fn = pfiles[ff]
        cf_prec = VisStimPrecedingSession(cf, fn)
        fp_d0_prec = VisStimRawFile(cf_prec, "adc_continuous")
        d0_nSamples = os.path.getsize(fp_d0_prec) / (nchan_adc * bytesPerSample)
        d0_ns_ar[ff] = d0_nSamples
        d0_dur_ar[ff] = d0_nSamples / sr_adc

        fp_d385_prec = VisStimRawFile(cf_prec, "probe_continuous")
        d385_nSamples = os.path.getsize(fp_d385_prec) / (nchan_probe * bytesPerSample)
        d385_dur_ar[ff] = d385_nSamples / sr

    d385_start_time = np.sum(d385_dur_ar)

    if sync2adc:
        cadc_f = VisStimAdcConcatFile(ks_path)
        cadc = LoadBinary(cadc_f, frequency=sr_adc, nChannels=12, channels=d1_chan)
        n_prec_samples = int(np.sum(d0_ns_ar))
        n_d1_samples = len(d1)
        ws = n_prec_samples
        wf = ws + n_d1_samples
        win = np.arange(ws, wf)
        d1_cadc = np.asarray(cadc[win], dtype=float)
        td1_cadc = (win + 1) / sr_adc

    if not use_events:
        td0 = np.arange(1, len(d0) + 1) / sr_adc

        plt.figure()
        i0 = td0 <= d1_dur
        plt.plot(td0[i0], d0[i0])
        plt.title("FG signal, ADC0")

        periods0, in0 = Threshold(np.column_stack([td0, d0.astype(float)]), ">", 10000, min=0.2)
        periods0_1col = periods0.reshape(-1)

        sweep_time = 20
        ns = int(d1_dur / sweep_time)
        blocks1 = np.arange(0, d1_dur, sweep_time)[:ns]
        blocks2 = np.arange(sweep_time, d1_dur + sweep_time, sweep_time)[:ns]

        nperiods = np.array([np.sum((periods0[:, 0] > blocks1[n]) & (periods0[:, 0] <= blocks2[n])) for n in range(ns)])
        p0_dif = periods0[:, 1] - periods0[:, 0]

        bf = np.cumsum(nperiods)
        bs = np.concatenate([[0], bf[:-1]])

        min_pts = np.array([np.argmin(p0_dif[int(bs[n]):int(bf[n])]) + int(bs[n]) for n in range(len(bs))])
        start_pts = min_pts + 1

        for i in range(len(start_pts)):
            idx = start_pts[i]
            w = p0_dif[idx]
            while p0_dif[idx + 1] > w or p0_dif[idx] < 0.4:
                start_pts[i] = idx + 1
                idx += 1
                w = p0_dif[idx]

        _, first_occurrence = np.unique(start_pts, return_index=True)
        start_pts = start_pts[np.sort(first_occurrence)]

        st_adc0 = periods0[start_pts, 0]
        et_adc0 = periods0[start_pts[1:], 0]
        plt.plot(st_adc0, np.ones(len(st_adc0)) * 11000, "gx", markersize=12)
    else:
        st_adc0 = snd0t

    td1 = np.arange(1, len(d1) + 1) / sr_adc

    plt.figure()
    plt.plot(td1, d1)
    plt.title("PD signal, ADC%d" % d1_chan)

    if sync2adc:
        plt.figure()
        plt.plot(td1_cadc, d1_cadc)
        plt.title("cADC PD signal, ADC%d" % d1_chan)
        td1 = td1_cadc
        d1 = d1_cadc

    if protocol == "Grating":
        py = 14000
        periods1, in1 = Threshold(np.column_stack([td1, d1.astype(float)]), ">", py, min=0.01)
        periods1 = periods1[(np.diff(periods1, axis=1)[:, 0] >= 1.5)]
        periods1 = periods1[(np.diff(periods1, axis=1)[:, 0] <= 2.5)]

        plt.plot(periods1[:, 0], np.ones(len(periods1)) * py, "gx")
        plt.plot(periods1[:, 1], np.ones(len(periods1)) * py, "rx")

        periods1 = np.fliplr(periods1)
        x1 = 3833.96959
        x2 = 4809.77611
        periods1 = np.column_stack([np.concatenate([[x1], periods1[:, 0]]), np.concatenate([periods1[:, 1], [x2]])])
        plt.plot(periods1[:, 0], np.ones(len(periods1)) * py, "bo")
        plt.plot(periods1[:, 1], np.ones(len(periods1)) * py, "bo")

    elif protocol == "Grating360":
        threshold = 15000
        isAboveThresholdMask = d1 < threshold

        maxGapSamples1 = 100
        maxGapSamples2 = 550

        plt.figure()
        plt.plot(isAboveThresholdMask)
        plt.ylim([-0.1, 1.1])
        plt.title("isAboveThresholdMask")

        plt.figure()
        plt.plot(td1, isAboveThresholdMask)
        plt.ylim([-0.1, 1.1])
        plt.title("isAboveThresholdMask")

        pd_noGap = fill_short_gaps(isAboveThresholdMask, maxGapSamples1)

        plt.figure()
        plt.plot(pd_noGap)
        plt.ylim([-0.1, 1.1])
        plt.title("pd_noGap")

        plt.figure()
        plt.plot(td1, pd_noGap)
        plt.ylim([-0.1, 1.1])
        plt.title("pd_noGap")

        pd_noGap_new = fill_short_gaps(~pd_noGap, maxGapSamples2)

        plt.figure()
        plt.plot(pd_noGap_new)
        plt.ylim([-0.1, 1.1])
        plt.title("PD, processed signal")

        plt.figure()
        plt.plot(td1, ~pd_noGap_new)
        plt.ylim([-0.1, 1.1])
        plt.title("PD, processed signal")

        py = 0.9
        periods1, in1 = Threshold(np.column_stack([td1, (~pd_noGap_new).astype(float)]), ">", py, min=1)
        periods1 = periods1[(np.diff(periods1, axis=1)[:, 0] >= 1.3)]
        periods1 = periods1[(np.diff(periods1, axis=1)[:, 0] <= 2.7)]

        plt.plot(periods1[:, 0], np.ones(len(periods1)) * py, "gx")
        plt.plot(periods1[:, 1], np.ones(len(periods1)) * py, "rx")
        plt.plot(periods1[:, 0], np.ones(len(periods1)) * py, "bo")
        plt.plot(periods1[:, 1], np.ones(len(periods1)) * py, "bo")

    elif protocol == "RFmapping":
        tf = 14000
        tr = 2000
        pf, _ = Threshold(np.column_stack([td1, d1.astype(float)]), "<", tf, min=0.07)
        pr, _ = Threshold(np.column_stack([td1, d1.astype(float)]), ">", tr, min=0.07)

        plt.plot(pf, np.ones(pf.shape) * tf, "gx")
        plt.plot(pr, np.ones(pr.shape) * tr, "rx")

        pf = pf[(np.diff(pf, axis=1)[:, 0] <= 0.15)]
        pr = pr[(np.diff(pr, axis=1)[:, 0] <= 0.15)]

        plt.plot(pf[:, 0], np.ones(len(pf)) * tf, "go")
        plt.plot(pr[:, 0], np.ones(len(pr)) * tr, "ro")

        x = 3802.18362
        pr = np.vstack([pr, [x, np.nan]])

        periods1 = np.column_stack([pf[:, 0], pr[:, 0]])
        plt.plot(periods1[:, 0], np.ones(len(periods1)) * tf, "bo")
        plt.plot(periods1[:, 1], np.ones(len(periods1)) * tr, "bo")

    elif protocol == "RFmapping360":
        threshold = 12000
        isAboveThresholdMask = d1 < threshold

        plt.figure()
        plt.plot(isAboveThresholdMask)
        plt.ylim([-0.1, 1.1])
        plt.title("isAboveThresholdMask")

        plt.figure()
        plt.plot(td1, isAboveThresholdMask)
        plt.ylim([-0.1, 1.1])
        plt.title("isAboveThresholdMask")

        pd_noGap = fill_short_gaps(isAboveThresholdMask, 400)

        plt.figure()
        plt.plot(td1, pd_noGap)
        plt.ylim([-0.1, 1.1])
        plt.title("pd_noGap")

        pd_noGap_new = fill_short_gaps(~pd_noGap, 1000)
        pd_noGap_new = ~pd_noGap_new

        plt.figure()
        plt.plot(td1, pd_noGap_new)
        plt.ylim([-0.1, 1.1])
        plt.title("PD, processed signal")

        tf = 0.9
        tr = 0.1
        pf, _ = Threshold(np.column_stack([td1, pd_noGap_new.astype(float)]), "<", tf, min=0.06)
        pr, _ = Threshold(np.column_stack([td1, pd_noGap_new.astype(float)]), ">", tr, min=0.06)

        plt.plot(pf, np.ones(pf.shape) * tf, "gx")
        plt.plot(pr, np.ones(pr.shape) * tr, "rx")

        pr = pr[1:, :]
        periods1 = np.column_stack([pf[:, 0], pr[:, 0]])

        plt.plot(pf[:, 0], np.ones(len(pf)) * tf, "bo")
        plt.plot(pr[:, 0], np.ones(len(pr)) * tr, "bo")

    elif protocol == "NaturalScenes":
        tf = 14000
        tr = 2000
        pf, _ = Threshold(np.column_stack([td1, d1.astype(float)]), "<", tf, min=0.2)
        pr, _ = Threshold(np.column_stack([td1, d1.astype(float)]), ">", tr, min=0.2)

        plt.plot(pf, np.ones(pf.shape) * tf, "gx")
        plt.plot(pr, np.ones(pr.shape) * tr, "rx")

        if stim_name != "ImageNet":
            pf = pf[(np.diff(pf, axis=1)[:, 0] <= 0.28)]
            pr = pr[(np.diff(pr, axis=1)[:, 0] <= 0.28)]

        plt.plot(pf[:, 0], np.ones(len(pf)) * tf, "go")
        plt.plot(pr[:, 0], np.ones(len(pr)) * tr, "ro")

        pf = pf[:, 0]
        pr = pr[:, 0]
        periods1 = np.column_stack([pf, pr])
        plt.plot(pf, np.ones(len(pf)) * tf, "bo")
        plt.plot(pr, np.ones(len(pr)) * tr, "bo")

    elif protocol == "Movies":
        tf = 15000
        tr = 2000
        min_ft = 0.02
        pf, _ = Threshold(np.column_stack([td1, d1.astype(float)]), "<", tf, min=min_ft)
        pr, _ = Threshold(np.column_stack([td1, d1.astype(float)]), ">", tr, min=min_ft)

        plt.plot(pf, np.ones(pf.shape) * tf, "gx")
        plt.plot(pr, np.ones(pr.shape) * tr, "rx")

        pr = pr[:, 0]
        pf = pf[:, 0]
        pr = np.delete(pr, [0, 1])
        pf = np.delete(pf, 0)

        plt.plot(pf, np.ones(len(pf)) * tf, "go")
        plt.plot(pr, np.ones(len(pr)) * tr, "ro")
        periods1 = np.column_stack([pf, pr])

    periods1_vector = periods1.reshape(-1)
    periods1_vector = periods1_vector[~np.isnan(periods1_vector)]

    if not use_events:
        td385 = np.arange(1, len(d385) + 1) / sr

        plt.figure()
        i385 = td385 <= d1_dur
        plt.plot(td385[i385], d385[i385])
        plt.ylim([-0.1, 1.1])
        plt.title("FG signal, Probe")

        periods385, in385 = Threshold(np.column_stack([td385, d385.astype(float)]), ">", 0.8, min=0.2)
        periods385_1col = periods385.reshape(-1)

        y1 = np.ones(periods385.shape) * 0.9
        y2 = np.ones(periods385.shape) * 0.88
        plt.plot(periods385, y1, "gx", markersize=12)
        plt.plot(periods385, y2, "rx", markersize=12)

        sweep_time = 20
        ns = int(d1_dur / sweep_time)
        blocks1 = np.arange(0, d1_dur, sweep_time)[:ns]
        blocks2 = np.arange(sweep_time, d1_dur + sweep_time, sweep_time)[:ns]

        nperiods = np.array([np.sum((periods385[:, 0] > blocks1[n]) & (periods385[:, 0] <= blocks2[n])) for n in range(ns)])
        p385_dif = periods385[:, 1] - periods385[:, 0]

        bf = np.cumsum(nperiods)
        bs = np.concatenate([[0], bf[:-1]])

        min_pts = np.array([np.argmin(p385_dif[int(bs[n]):int(bf[n])]) + int(bs[n]) for n in range(len(bs))])
        start_pts = min_pts + 1
        if start_pts[-1] >= len(periods385):
            start_pts = start_pts[:-1]

        for i in range(len(start_pts)):
            idx = start_pts[i]
            w = p385_dif[idx]
            while p385_dif[idx + 1] > w or p385_dif[idx] < 0.4:
                start_pts[i] = idx + 1
                idx += 1
                w = p385_dif[idx]

        _, first_occurrence = np.unique(start_pts, return_index=True)
        start_pts = start_pts[np.sort(first_occurrence)]

        st_probe = periods385[start_pts, 0]
        et_probe = periods385[start_pts[1:], 0]

        y1 = np.ones(len(st_probe)) * 0.9
        y2 = np.ones(len(et_probe)) * 0.88
        plt.plot(st_probe, y1, "gx", markersize=12)
        plt.plot(et_probe, y2, "rx", markersize=12)

        npulses = min(len(st_adc0), len(st_probe))
        st_adc0 = st_adc0[:npulses]
        st_probe = st_probe[:npulses]
    else:
        st_probe = snd385t

    if not sync2adc:
        Vq = interp1(st_adc0, st_probe, periods1_vector)
        Vq = Vq + d385_start_time
    else:
        Vq = periods1_vector

    Vq_periods = np.column_stack([Vq[0:-1:2], Vq[1::2]])
    if protocol in ["Grating", "Grating360"]:
        Vq = Vq_periods

    return stimInfo, Vq

def GratingAnalysis_EB(ks_path, fn, trials, GratingTimes, mode_sel, settingsFile, sync2adc, guo):
    save_dir = VisStimMatFile("Grating_data")
    EnsureDir(fullfile(save_dir, "PSTH_Data"))
    EnsureDir(fullfile(save_dir, "Summary_Figures"))
    EnsureDir(fullfile(save_dir, "Grating_info"))
    save_data = fullfile(save_dir, "PSTH_Data", fn)
    save_pdf = fullfile(save_dir, "Summary_Figures", fn)
    save_info = fullfile(save_dir, "Grating_info", fn)

    wfp = VisStimMatFile("SpikeWaveforms", "mean_wfs", fn + ".mat")
    if os.path.isfile(wfp):
        meanWaveforms = load_mat_file(wfp)["meanWaveforms"]
        plot_wf = True
    else:
        meanWaveforms = None
        plot_wf = False

    stimnum = length(trials)
    orinum = 12
    repnum = stimnum / orinum
    tf = get_trial_field(trials, 0, "Temporal_Frequency")
    npulses = length(GratingTimes)

    passes_dif = stimnum - npulses
    if passes_dif == 0:
        print("Number of passes = number of pulses")
    elif passes_dif < 0:
        print("Number of pulses > number of passes")
    else:
        repnum = math.floor(npulses / orinum)
        stimnum = orinum * repnum

    OrientTiming = []
    for i in range(orinum):
        ori_rows = [r for r in range(stimnum) if get_trial_field(trials, r, "Orientation") == 30 * i]
        OrientTiming.append(np.asarray(GratingTimes)[ori_rows, 0])

    stim_dur = np.mean(np.diff(GratingTimes, axis=1))
    limit = np.array([-1, stim_dur + 1])
    win_dur = np.sum(np.abs(limit))
    bin_size = 0.035
    numbin = math.floor(win_dur / bin_size)
    dirs = np.arange(0, 360, 30)

    if "Mouse" in fn:
        sn_match = re.search(r"Mouse\d+_\d{8}_\d+to\d+", fn)
        sn = sn_match.group(0)
    else:
        sn = fn

    labels_file = VisStimMatFile("New_Unit_Labels", sn + ".mat")
    new_labels = load_mat_file(labels_file)["new_labels"]

    spike_clusters = readNPY(VisStimInputFile("spike_clusters"))
    uc = np.unique(spike_clusters)

    if guo:
        good_idx = np.asarray(get_column(new_labels, "KSLabel")) == "good"
        unit_list = np.asarray(get_column(new_labels, "cluster_id"))[good_idx]
    else:
        unit_list = uc

    unit_num = len(unit_list)
    sel = range(unit_num)

    if mode_sel == "sum":
        sd = save_data + "_SumOfSpikes.mat"
        sn_pdf = save_pdf + "_SumOfSpikes"
    else:
        sd = save_data + ".mat"
        sn_pdf = save_pdf + "_SpikeRate"

    if os.path.isfile(save_data + ".mat"):
        UnitFeature = load_mat_file(save_data + ".mat")["UnitFeature"]
    else:
        sr = 30000
        if sync2adc:
            spike_times = readNPY(VisStimInputFile("adc_spike_times"))
        else:
            spike_times = readNPY(VisStimInputFile("spike_times"))
            spike_times = (spike_times + 1).astype(float) / sr

        hist_all = np.zeros((unit_num, orinum, numbin))
        n_phases = 4
        phase_window = 2 * math.pi / n_phases * 1.5
        CyclePhaseFR = np.zeros((unit_num, orinum, n_phases))
        t = None

        for i in sel:
            u = unit_list[i]
            s = spike_times[spike_clusters == u]

            for ori in range(orinum):
                events = OrientTiming[ori]
                sync, si = Sync(s, events, durations=limit)
                if mode_sel == "sum":
                    hist = len(sync) / repnum
                else:
                    hist, t = SyncHist(sync, si, durations=limit, number_of_bins=numbin, mode=mode_sel)

                if len(np.atleast_1d(hist)) != 0:
                    hist_all[i, ori, :] = hist
                    t_stim = t[(t >= 0) & (t <= stim_dur)]
                    hist_stim = hist[(t >= 0) & (t <= stim_dur)]
                    baseFR = np.mean(hist[t < 0])

                    total_cyc = int(tf * round(stim_dur))
                    cyc_dur = 1 / tf
                    phase_dur = cyc_dur / n_phases
                    cyc_id = np.floor(t_stim / cyc_dur).astype(int) + 1
                    inst_phase = np.mod(2 * math.pi * tf * t_stim, 2 * math.pi)
                    bin_centers = np.linspace(0, 2 * math.pi, n_phases + 1)[:-1]

                    phase_means = np.zeros((total_cyc, n_phases))
                    for cc in range(1, total_cyc + 1):
                        cyc_mask = cyc_id == cc
                        for b in range(n_phases):
                            center = bin_centers[b]
                            delta_phase = np.angle(np.exp(1j * (inst_phase - center)))
                            bin_mask = np.abs(delta_phase) <= phase_window / 2
                            phase_means[cc - 1, b] = np.nanmean(hist_stim[cyc_mask & bin_mask] - baseFR)

                    CyclePhaseFR[i, ori, :] = np.nanmean(phase_means, axis=0)

            print("done %d out of %d" % (i + 1, unit_num))

        UnitFeature = {"hist_t": t, "hist": hist_all, "CyclePhaseFR": CyclePhaseFR}
        save_mat_file(sd, UnitFeature=UnitFeature)

    plt.close("all")

    sn_pdf = sn_pdf + "_Summary_New.pdf"
    if os.path.isfile(sn_pdf):
        os.remove(sn_pdf)

    si_file = save_info + "_New.mat"
    if os.path.isfile(si_file):
        os.remove(si_file)

    hist_t = field(UnitFeature, "hist_t")
    sp_pos = [0, 2, 4, 6, 8, 10, 1, 3, 5, 7, 9, 11]
    hist_t_stim = hist_t[(hist_t >= 0) & (hist_t <= stim_dur)]
    remap = [6, 5, 4, 3, 2, 1, 0, 11, 10, 9, 8, 7]

    data = {
        "dir": dirs,
        "uid": np.zeros(unit_num),
        "R_mean": np.zeros((unit_num, orinum)),
        "R_mean_minus_bl": np.zeros((unit_num, orinum)),
        "R_pk_minus_bl": np.zeros((unit_num, orinum)),
        "DSI": np.zeros(unit_num),
        "pd_rad": np.zeros(unit_num),
        "pd_deg": np.zeros(unit_num),
        "OSI": np.zeros(unit_num),
        "po_rad": np.zeros(unit_num),
        "po_deg": np.zeros(unit_num),
    }

    pp = PdfPages(sn_pdf)
    for i in sel:
        uid = unit_list[i]
        fig = plt.figure(figsize=(8.3, 10.6))
        fig.suptitle("Unit " + str(int(uid)))

        psth_axes = []
        phase_axes = []
        for j in range(orinum):
            ax = fig.add_subplot((orinum // 2) + 1, 2, sp_pos[j] + 1)
            psth_axes.append(ax)
            h = field(UnitFeature, "hist")[i, j, :]
            ax.bar(hist_t, h, width=np.mean(np.diff(hist_t)), color=[0.2, 0.2, 0.2], edgecolor=[0.2, 0.2, 0.2])
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.set_ylim(bottom=-1)
            bl = np.mean(h[hist_t < 0])
            sdv = np.std(h[hist_t < 0.5])
            bl2sd = bl + (2 * sdv)
            if ax.get_ylim()[1] < bl2sd:
                ax.set_ylim(top=ax.get_ylim()[1] + 10)
            ax.plot(ax.get_xlim()[0] + 0.0001, bl, "bx", markersize=5)
            ax.plot(ax.get_xlim()[0] + 0.0001, bl2sd, "x", color=rgb("purple"), markersize=5)
            if j == 0:
                ax.set_ylabel("imp/s")
            elif j == orinum // 2 - 1:
                ax.set_xlabel("time (s)")

            pos = ax.get_position()
            arrow_ax = fig.add_axes([pos.x0 + 0.005, pos.y0 + 0.07, 0.022, 0.015])
            angle_deg = dirs[remap[j]]
            angle_rad = np.deg2rad(angle_deg)
            arrow_ax.quiver(0, 0, np.cos(angle_rad), np.sin(angle_rad), angles="xy", scale_units="xy", scale=1)
            arrow_ax.axis("equal")
            arrow_ax.axis("off")

            ph_curve = field(UnitFeature, "CyclePhaseFR")[i, j, :]
            ph_ax = fig.add_axes([pos.x0 + pos.width * 0.82, pos.y0 + pos.height * 0.82, pos.width * 0.18, pos.height * 0.28])
            phase_axes.append(ph_ax)
            px = np.array([0, 90, 180, 270])
            ph_ax.plot(px, ph_curve, "-o", color=[0.2, 0.2, 0.2], markersize=4)
            if j == 0:
                ph_ax.set_xticks(px)
            else:
                ph_ax.set_xticks(px, [])
            ph_ax.spines["top"].set_visible(False)
            ph_ax.spines["right"].set_visible(False)

        mxy = max(ax.get_ylim()[1] for ax in psth_axes)
        for ax in psth_axes:
            ax.set_ylim([-1, mxy])

        if plot_wf:
            hist7_ax = psth_axes[5]
            pos = hist7_ax.get_position()
            wf_ax = fig.add_axes([pos.x0, pos.y0 + pos.height * 1.13, pos.width * 0.25, pos.height * 0.4])
            u_idx = int(np.where(field(meanWaveforms, "unitIds") == uid)[0][0])
            ch_idx = int(field(meanWaveforms, "ptp_chan_idx")[u_idx]) - 1
            wf = field(meanWaveforms, "data")[ch_idx, :, u_idx]
            wf_ax.plot(field(meanWaveforms, "timepts"), wf, color=[0.2, 0.2, 0.2])
            yl1 = 1.3 * wf_ax.get_ylim()[0]
            wf_ax.plot([0, 1], [yl1, yl1], "k-")
            wf_ax.text(0.25, 1.4 * yl1, "1 ms", fontsize=8)
            wf_ax.axis("off")

        ph_curves = field(UnitFeature, "CyclePhaseFR")[i, :, :]
        yl1 = min(0, math.floor(np.nanmin(ph_curves)))
        yl1 = yl1 - (2 - (yl1 % 2))
        yl2 = math.ceil(np.nanmax(ph_curves))
        yl2 = yl2 + (2 - (yl2 % 2))
        for ph_ax in phase_axes:
            ph_ax.set_ylim([yl1, yl2])

        A = mxy * 0.1
        stim_wave = A * np.cos((2 * math.pi * tf) * hist_t_stim) + A
        for ax in psth_axes:
            ax.plot(hist_t_stim, stim_wave, color=rgb("gray"))
        for n, ax in enumerate(psth_axes):
            ax.text(ax.get_xlim()[0] + 0.1, mxy, "%d deg" % dirs[n], fontsize=8)

        h = field(UnitFeature, "hist")[i, 0:orinum, :]
        bl = np.mean(h[:, hist_t < 0], axis=1)
        h_mean = np.mean(h[:, (hist_t >= 0) & (hist_t < stim_dur)], axis=1)
        h_mean_minus_bl = h_mean - bl
        h_pk = np.max(h[:, (hist_t >= 0) & (hist_t < stim_dur)], axis=1)
        h_pk_minus_bl = h_pk - bl
        R = h_mean_minus_bl.copy()
        yd = np.column_stack([h_mean, h_mean_minus_bl, h_pk_minus_bl])
        yd = np.vstack([yd, yd[0, :]])
        yd[yd < 0] = 0
        theta_rad = np.deg2rad(np.concatenate([dirs, [dirs[0]]]))

        R[R < 0] = 0
        tr = theta_rad[:orinum]
        DSI_complex = np.sum(R * np.exp(1j * tr)) / np.sum(R)
        DSI = abs(DSI_complex)
        pd_rad = np.angle(DSI_complex)
        pd_deg = round(np.mod(np.rad2deg(pd_rad), 360))

        R_orient = np.zeros(orinum // 2)
        theta_orient = np.zeros(orinum // 2)
        for k in range(orinum // 2):
            opp = (k + orinum // 2) % orinum
            R_orient[k] = (R[k] + R[opp]) / 2
            theta_orient[k] = theta_rad[k]

        vec = np.sum(R_orient * np.exp(1j * 2 * theta_orient))
        OSI = abs(vec) / np.sum(R_orient)
        po_rad = np.angle(vec) / 2
        po_deg = np.mod(np.rad2deg(po_rad), 180)

        data["uid"][i] = uid
        data["R_mean"][i, :] = h_mean
        data["R_mean_minus_bl"][i, :] = R
        data["R_pk_minus_bl"][i, :] = h_pk_minus_bl
        data["DSI"][i] = DSI
        data["pd_rad"][i] = pd_rad
        data["pd_deg"][i] = pd_deg
        data["OSI"][i] = OSI
        data["po_rad"][i] = po_rad
        data["po_deg"][i] = po_deg

        titles = ["Mean FR", "Mean FR-baseline", "Peak FR-baseline"]
        for k in range(3):
            ax = fig.add_subplot((orinum // 2) + 1, 3, (orinum // 2) * 3 + k + 1, projection="polar")
            ax.plot(theta_rad, yd[:, k])
            ax.set_theta_zero_location("W")
            ax.set_theta_direction(-1)
            ax.set_title(titles[k], fontsize=9)
            if k == 1:
                ax.plot([0, pd_rad], [0, DSI * max(R)], color=[0, 0.8, 0], linewidth=1.2)

        if plot_wf:
            PlotProbeConfig("Grating", ch_idx + 1, settingsFile)

        pp.savefig(fig)
        plt.close(fig)

    pp.close()
    save_mat_file(si_file, data=data)

def RFmapping_EB(ks_path, fn, trials, Vq, mode_sel, nbins, save_figs, save_gauss, gauss_2d_data, gdi, settingsFile, sync2adc, guo):
    plt.close("all")
    save_dir = VisStimMatFile("RF_maps")
    EnsureDir(fullfile(save_dir, "Spike_Data"))
    EnsureDir(fullfile(save_dir, "Summary_Figures"))
    EnsureDir(fullfile(save_dir, "Gaussian_Data"))
    save_data = fullfile(save_dir, "Spike_Data", fn)
    save_pdf = fullfile(save_dir, "Summary_Figures", fn)
    save_gd = fullfile(save_dir, "Gaussian_Data", fn)
    meanWaveforms = load_mat_file(VisStimMatFile("SpikeWaveforms", "mean_wfs", fn + ".mat"))["meanWaveforms"]

    chids, xpos, ypos = parse_channel_layout(settingsFile)
    channel_map = chids

    Vq = np.asarray(Vq).reshape(-1)
    npulses = len(Vq)
    last_offset = Vq[-1] + np.mean(np.diff(Vq))
    Vq_periods = np.column_stack([Vq, np.concatenate([Vq[1:], [last_offset]])])

    nframes = length(trials)
    PosX = np.array([get_trial_field(trials, i, "Square_PositionX") for i in range(nframes)])
    PosY = np.array([get_trial_field(trials, i, "Square_PositionY") for i in range(nframes)])
    Lum = np.array([get_trial_field(trials, i, "Square_Luminance") for i in range(nframes)])
    SquareSize = get_trial_field(trials, 0, "Square_Size")

    sr = 30000
    if sync2adc:
        spike_times = readNPY(VisStimInputFile("adc_spike_times"))
    else:
        spike_times = readNPY(VisStimInputFile("spike_times"))
        spike_times = (spike_times + 1).astype(float) / sr

    spike_clusters = readNPY(VisStimInputFile("spike_clusters"))
    uc = np.unique(spike_clusters)

    if "Mouse" in fn:
        sn_match = re.search(r"Mouse\d+_\d{8}_\d+to\d+", fn)
        sn = sn_match.group(0)
    else:
        sn = fn

    labels_file = VisStimMatFile("New_Unit_Labels", sn + ".mat")
    new_labels = load_mat_file(labels_file)["new_labels"]

    if guo:
        good_idx = np.asarray(get_column(new_labels, "KSLabel")) == "good"
        unit_list = np.asarray(get_column(new_labels, "cluster_id"))[good_idx]
    else:
        unit_list = uc
    unit_num = len(unit_list)
    sel = range(unit_num)

    SqDeg = SquareSize
    x_num = len(np.unique(PosX))
    y_num = len(np.unique(PosY))
    u_xy = len(np.unique(np.column_stack([PosX, PosY, Lum]), axis=0))
    xy_ratio = x_num / y_num
    repnum = nframes / u_xy

    passes_dif = nframes - npulses
    if passes_dif == 0:
        print("Number of passes = number of pulses")
    elif passes_dif < 0:
        print("Number of pulses > number of passes")
    else:
        repnum = math.floor(npulses / u_xy)
        nframes = u_xy * repnum
        Vq_periods = Vq_periods[:nframes, :]
        PosX = PosX[:nframes]
        PosY = PosY[:nframes]
        Lum = Lum[:nframes]

    ss = SquareSize

    if mode_sel == "sum":
        sd = save_data + "_RFmap_SumOfSpikes.mat"
        sn_pdf = save_pdf + "_Maps_SumOfSpikes.pdf"
    else:
        sd = save_data + "_RFmap_SpikeRate.mat"
        sn_pdf = save_pdf + "_Maps_SpikeRate.pdf"

    if os.path.isfile(sd):
        RFmap = load_mat_file(sd)["RFmap"]
    else:
        RFmap = []
        for k in range(unit_num):
            RFmap.append({"ON": {"OnSet": np.zeros((y_num, x_num, nbins))}, "OFF": {"OnSet": np.zeros((y_num, x_num, nbins))}, "baseline": 0})

        for k in sel:
            u = unit_list[k]
            s = spike_times[spike_clusters == u]
            sync, i = Sync(s, Vq_periods[0, 0], durations=np.array([-5, 0]))
            baseline, _ = SyncHist(sync, i, durations=np.array([-5, 0]), number_of_bins=1, mode=mode_sel)
            if len(baseline) == 0:
                baseline = 0
            else:
                baseline = baseline[0]
            RFmap[k]["baseline"] = baseline

            for x in range(x_num):
                for y in range(y_num):
                    curX = -(x_num - 1) / 2 * SqDeg + x * SqDeg
                    curY = -(y_num - 1) / 2 * SqDeg + y * SqDeg
                    IDon = (PosX == curX) & (PosY == curY) & (Lum == 1)
                    IDoff = (PosX == curX) & (PosY == curY) & (Lum == 0)

                    sync, i = Sync(s, Vq_periods[IDon, 0], durations=np.array([0, 0.1]))
                    hist, _ = SyncHist(sync, i, durations=np.array([0, 0.1]), number_of_bins=nbins, mode=mode_sel)
                    if len(hist) != 0:
                        RFmap[k]["ON"]["OnSet"][y, x, :] = hist

                    sync, i = Sync(s, Vq_periods[IDoff, 0], durations=np.array([0, 0.1]))
                    hist, _ = SyncHist(sync, i, durations=np.array([0, 0.1]), number_of_bins=nbins, mode=mode_sel)
                    if len(hist) != 0:
                        RFmap[k]["OFF"]["OnSet"][y, x, :] = hist

            print("done %d out of %d" % (k + 1, unit_num))

        save_mat_file(sd, RFmap=RFmap)

    xdeg = np.unique(PosX)
    ydeg = np.unique(PosY)

    if len(gauss_2d_data) == 0:
        if save_figs and os.path.isfile(sn_pdf):
            os.remove(sn_pdf)

        gauss_2d_data = [None] * len(list(sel))
        pp = PdfPages(sn_pdf) if save_figs else None
        for k in sel:
            u = unit_list[k]
            u_idx = int(np.where(field(meanWaveforms, "unitIds") == u)[0][0])
            ch_idx = int(field(meanWaveforms, "ptp_chan_idx")[u_idx]) - 1
            wf = field(meanWaveforms, "data")[ch_idx, :, u_idx]
            chans_idx = np.arange(ch_idx - 2, ch_idx + 3)
            chans_idx = chans_idx[(chans_idx >= 0) & (chans_idx < 384)]
            nwfs = field(meanWaveforms, "data")[chans_idx, :, u_idx]

            fig = plt.figure(figsize=(6.7, 6.7))
            maps = [RFmap[k]["ON"]["OnSet"], RFmap[k]["OFF"]["OnSet"]]
            titles = ["ON stim RF map", "OFF stim RF map"]
            gauss_2d = [None, None]
            baseline = RFmap[k]["baseline"]
            clims = []

            for ii in range(2):
                ax = fig.add_subplot(2, 2, ii + 1)
                if mode_sel == "sum":
                    rf = np.sum(maps[ii], axis=2) - baseline
                else:
                    rf = np.mean(maps[ii], axis=2) - baseline
                    mx_fr = np.max(rf)
                    min_fr = np.min(rf)
                    if mx_fr > 0 and mx_fr > abs(min_fr):
                        kc_sign = 1
                    else:
                        kc_sign = -1

                im = ax.imshow(rf, extent=[xdeg[0], xdeg[-1], ydeg[-1], ydeg[0]], aspect="auto", cmap="gray")
                plt.colorbar(im, ax=ax, fraction=0.03)
                ax.axis("off")
                ax.plot([ax.get_xlim()[1], ax.get_xlim()[1] - 30], [ax.get_ylim()[0], ax.get_ylim()[0]], "r-", linewidth=1.2)
                ax.set_title(titles[ii])
                if ii == 0:
                    ax.text(ax.get_xlim()[1] - 15, ax.get_ylim()[0] + 5, "30 deg")
                clims.append(im.get_clim())

                xg, yg = np.meshgrid(xdeg, ydeg)
                xy = np.column_stack([xg.reshape(-1), yg.reshape(-1)])

                kc = np.max(rf)
                cx = 0
                sdx = 3 * SqDeg
                cy = 0
                b = 0
                theta = 0
                r = 1

                p0 = np.array([kc, cx, sdx, cy, b, theta, r], dtype=float)
                LB = np.array([0, min(xdeg) - 2 * SqDeg, SqDeg, min(ydeg) - 2 * SqDeg, 0, -math.pi / 2, 0.6], dtype=float)
                UB = np.array([3 * kc, max(xdeg) + 2 * SqDeg, 0.5 * max(ydeg), max(ydeg) + 2 * SqDeg, 0.25 * kc, math.pi / 2, 1.9], dtype=float)

                if kc_sign == -1:
                    kc = np.min(rf)
                    p0[0] = kc
                    LB[[0, 4]] = [3 * kc, 0.25 * kc]
                    UB[[0, 4]] = [0, 0]

                def residual(p):
                    return gauss_2d_model(p, xy[:, 0], xy[:, 1]) - rf.reshape(-1)

                pfit = least_squares(residual, p0, bounds=(LB, UB)).x
                sdy = pfit[6] * pfit[2]

                txt = "k: %0.1f imp/s, sx: %0.1f deg, sy: %0.1f deg\nTheta: %0.1f deg, sign: %d" % (
                    pfit[0], pfit[2], sdy, np.rad2deg(pfit[5]), kc_sign
                )

                gauss_2d[ii] = {
                    "pfit": np.concatenate([pfit, [sdy]]),
                    "pfit_labels": ["k(imp/s)", "cx(deg)", "sx(deg)", "cy(deg)", "baseline(imp/s)", "theta(rad)", "sy/sx", "sy(deg)"],
                    "rf_fit": gauss_2d_model(pfit, xg, yg),
                }
                ax.text(ax.get_xlim()[0], 1.5 * ax.get_ylim()[0], txt)

            all_axes = fig.axes
            cmx = max(c[1] for c in clims)
            cmin = min(c[0] for c in clims)
            for ax in all_axes[:2]:
                ax.images[0].set_clim(cmin, cmx)

            t = np.linspace(0, 2 * math.pi, 300)
            for jj in range(2):
                ax = all_axes[jj]
                pfit = gauss_2d[jj]["pfit"]
                xe = pfit[2] * np.cos(t)
                ye = pfit[7] * np.sin(t)
                theta = pfit[5]
                Rm = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
                xy_rot = Rm @ np.vstack([xe, ye])
                ax.plot(xy_rot[0, :] + pfit[1], xy_rot[1, :] + pfit[3], "b", linewidth=1)

            fig.suptitle("Unit %d" % u)

            wf_ax = fig.add_subplot(2, 2, 4)
            yshift = 1000
            for ww in range(len(chans_idx)):
                if chans_idx[ww] == ch_idx:
                    wf_ax.plot(field(meanWaveforms, "timepts"), nwfs[ww, :] + ww * yshift, color=rgb("DodgerBlue"))
                else:
                    wf_ax.plot(field(meanWaveforms, "timepts"), nwfs[ww, :] + ww * yshift, color=[0.2, 0.2, 0.2])
            yl1 = wf_ax.get_ylim()[0] - 1
            wf_ax.plot([0, 0.001], [yl1, yl1], "k-")
            wf_ax.text(0.00025, yl1 - 2, "1 ms", fontsize=8)
            wf_ax.axis("off")
            for w in range(len(chans_idx)):
                wf_ax.text(1.1 * wf_ax.get_xlim()[1], w * yshift, str(int(channel_map[chans_idx[w]])))

            PlotProbeConfig("RFmapping", ch_idx + 1, settingsFile)
            gauss_2d_data[k] = gauss_2d

            if save_figs:
                pp.savefig(fig)
                plt.close(fig)

        if save_figs:
            pp.close()
        if save_gauss:
            save_mat_file(save_gd, gauss_2d_data=gauss_2d_data)
    else:
        idx_data = load_mat_file(gdi)

    fig = plt.figure(figsize=(6.7, 6.7))
    colors = distinguishable_colors(len(list(sel)))
    handles = []
    labels = []
    titles = ["ON stimulus", "OFF stimulus"]
    for ss in range(2):
        ax = fig.add_subplot(2, 2, ss + 1)
        ax.set_xlim([xdeg[0], xdeg[-1]])
        ax.set_ylim([ydeg[0], ydeg[-1]])
        for kk in range(len(gauss_2d_data)):
            pfit = gauss_2d_data[kk][ss]["pfit"]
            t = np.linspace(0, 2 * math.pi, 300)
            xe = pfit[2] * np.cos(t)
            ye = pfit[7] * np.sin(t)
            theta = pfit[5]
            Rm = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
            xy_rot = Rm @ np.vstack([xe, ye])
            line, = ax.plot(xy_rot[0, :] + pfit[1], -(xy_rot[1, :] + pfit[3]), color=colors[kk, :], linewidth=1)
            handles.append(line)
            labels.append(str(kk + 1))
        ax.set_xlim([xdeg[0], xdeg[-1]])
        ax.set_ylim([ydeg[0], ydeg[-1]])
        ax.set_title(titles[ss])

    fig.legend(handles, labels, loc="lower center", ncol=4)
    fig.axes[1].set_xlabel("deg")
    fig.axes[1].set_ylabel("deg")

    if save_figs:
        exportgraphics(fig, sn_pdf, append=True)
        close_pdf(sn_pdf)
        plt.close(fig)

def DepthSort_meanWaveForms(ks_path, concat_f, session, settingsFile, elec, guo, nchan, *args):
    sbefore = 60
    safter = 60
    sample_num = 1000
    totalch = 384
    sr = 30000

    ntp = sbefore + safter
    tp = (np.arange(1, ntp + 1) / sr) * 1000

    if len(args) != 0:
        dim = np.asarray(args[0]).reshape(-1)
    else:
        dim = np.zeros(np.size(elec))

    elec = np.asarray(elec).reshape(-1)
    if len(elec) > 1:
        for eIx in range(len(elec)):
            DepthSort_meanWaveForms(ks_path, concat_f, session, settingsFile, elec[eIx], guo, nchan, dim[eIx])
    else:
        print("Sorting electrode %i of %s" % (elec[0], session))

        clu = readNPY(VisStimInputFile("spike_clusters"))
        spktimes = readNPY(VisStimInputFile("spike_times")) + 1
        dat_numel = os.path.getsize(concat_f) // 2

        labels_file = VisStimMatFile("New_Unit_Labels", session + ".mat")
        new_labels = load_mat_file(labels_file)["new_labels"]

        if guo:
            good_idx = np.asarray(get_column(new_labels, "KSLabel")) == "good"
            unit_list = np.asarray(get_column(new_labels, "cluster_id"))[good_idx]
        else:
            unit_list = np.unique(clu)
        clu_num = len(unit_list)

        chids, xpos, ypos = parse_channel_layout(settingsFile)
        chanmap = chids.astype(int)

        min_chan_idx = np.zeros(clu_num)
        ptp_chan_idx = np.zeros(clu_num)
        SpkAmpPrfl = np.zeros((totalch, clu_num))

        meanwavs = np.zeros((totalch, safter + sbefore, clu_num))
        with open(concat_f, "rb") as fid_dat:
            for ii in range(clu_num):
                cluspkIdx = np.where(clu == unit_list[ii])[0]
                nSpk_clu = np.sum(clu == unit_list[ii])
                SampleStep = math.floor(nSpk_clu / sample_num)
                if SampleStep > 0:
                    sampleIdx = np.arange(0, SampleStep * sample_num, SampleStep)
                    sampleList = cluspkIdx[sampleIdx]
                else:
                    sampleList = cluspkIdx

                if len(sampleList) != 0:
                    wav = np.zeros((totalch, safter + sbefore, len(sampleList)))
                    for jj in range(len(sampleList)):
                        sample = sampleList[jj]
                        st = spktimes[sample]

                        start_idx = (float(st) - sbefore) * nchan + 1
                        end_idx = (float(st) + safter) * nchan

                        if start_idx < 1 or end_idx > dat_numel:
                            print("Index exceeds the number of array elements: ii %d, jj %d" % (ii + 1, jj + 1))
                            continue

                        n_to_read = int(end_idx - start_idx + 1)
                        fid_dat.seek(int((start_idx - 1) * 2), os.SEEK_SET)
                        w = np.fromfile(fid_dat, dtype=np.int16, count=n_to_read)
                        if len(w) != n_to_read:
                            print("Could not read full waveform: ii %d, jj %d" % (ii + 1, jj + 1))
                            continue

                        wvforms = np.reshape(w, (nchan, -1), order="F")
                        wvforms = wvforms[chanmap, :]
                        wvforms = wvforms - np.median(wvforms, axis=1, keepdims=True)
                        wav[:, :, jj] = wvforms

                    mwav = np.squeeze(np.mean(wav, axis=2))
                    meanwavs[:, :, ii] = mwav

                    ptp = np.max(mwav, axis=1) - np.min(mwav, axis=1)
                    max_chan_idx = int(np.argmax(ptp))
                    ptp_chan_idx[ii] = max_chan_idx + 1

                print("%d out of %d units" % (ii + 1, clu_num))

        meanWaveforms = {
            "data": meanwavs,
            "samplenum": sample_num,
            "sbefore": sbefore,
            "safter": safter,
            "unitIds": unit_list,
            "chanmap": chanmap,
            "min_chan_idx": min_chan_idx,
            "ptp_chan_idx": ptp_chan_idx,
            "timepts": tp,
            "xpos": xpos,
            "ypos": ypos,
        }

        save_wfs = VisStimMatFile("SpikeWaveforms", "mean_wfs", session + ".mat")
        EnsureParentDir(save_wfs)
        save_mat_file(save_wfs, meanWaveforms=meanWaveforms)


def VisStimConfig():
    cfg = SimpleNamespace()
    cfg.gratingStimFile = ""

    cfg.sessionFolder = "260603_14"
    cfg.probeLetter = "B"
    cfg.session = cfg.sessionFolder + "_Probe" + cfg.probeLetter
    oneBoxStream = "OneBox-103"

    cfg.gratingSpreadsheetRow = 10
    cfg.rfSpreadsheetRow = 1

    resfile_folder_path = "R:\\"
    date = "260603\\"
    data_folder = "data\\260603\\260603_4\\"

    recording_folder = data_folder + date + "Record Node 102\\"
    pipeline_folder = data_folder + "pipeline/" + cfg.sessionFolder + "/Probe" + cfg.probeLetter + "/"
    cfg.rfStimFile = data_folder + "260603.mat"
    cfg.adcConcatRawFile = data_folder + "pipeline/" + cfg.sessionFolder + "/OneBox-ADC/concat/traces_cached_seg0.raw"

    kilosort_folder = pipeline_folder + "kilosort\\"
    probe_concat_folder = pipeline_folder + "concat\\"

    cfg.outputMatFilesFolder = "R:\\Kai\\MatlabApps\\MatFiles"

    cfg.gratingSpreadsheetFile = resfile_folder_path + "Kai\\MatlabApps\\360\\Info_Grating.xlsx"
    cfg.rfSpreadsheetFile = resfile_folder_path + "Kai\\MatlabApps\\360\\Info_RFmapping.xlsx"

    cfg.settingsFile = recording_folder + "settings.xml"

    cfg.clusterKSLabelFile = kilosort_folder + "cluster_KSLabel.tsv"
    cfg.clusterGroupFile = kilosort_folder + "cluster_group.tsv"
    cfg.spikeClustersFile = kilosort_folder + "spike_clusters.npy"
    cfg.spikeTimesFile = kilosort_folder + "spike_times.npy"

    cfg.adcSpikeTimesFile = cfg.spikeTimesFile

    cfg.probeConcatRawFile = probe_concat_folder + "traces_cached_seg0.raw"

    cfg.adcContinuousFile = recording_folder + "experiment1\\recording1\\continuous\\" + oneBoxStream + ".OneBox-ADC\\continuous.dat"
    cfg.adcContinuousSampleNumbersFile = recording_folder + "experiment1\\recording1\\continuous\\" + oneBoxStream + ".OneBox-ADC\\sample_numbers.npy"
    cfg.adcTtlTimestampsFile = recording_folder + "experiment1\\recording1\\events\\" + oneBoxStream + ".OneBox-ADC\\TTL\\timestamps.npy"
    cfg.adcTtlSampleNumbersFile = recording_folder + "experiment1\\recording1\\events\\" + oneBoxStream + ".OneBox-ADC\\TTL\\sample_numbers.npy"

    cfg.probeContinuousFile = recording_folder + "experiment1\\recording1\\continuous\\" + oneBoxStream + ".Probe" + cfg.probeLetter + "\\continuous.dat"
    cfg.probeContinuousSampleNumbersFile = recording_folder + "experiment1\\recording1\\continuous\\" + oneBoxStream + ".Probe" + cfg.probeLetter + "\\sample_numbers.npy"
    cfg.probeTtlTimestampsFile = recording_folder + "experiment1\\recording1\\events\\" + oneBoxStream + ".Probe" + cfg.probeLetter + "\\TTL\\timestamps.npy"
    cfg.probeTtlSampleNumbersFile = recording_folder + "experiment1\\recording1\\events\\" + oneBoxStream + ".Probe" + cfg.probeLetter + "\\TTL\\sample_numbers.npy"

    cfg.precedingSessionFiles = []
    cfg.fmaToolboxRoot = resfile_folder_path + "Kai\\MatlabApps\\buzcode\\externalPackages\\FMAToolbox"
    return cfg


def VisStimMatFile(*args):
    cfg = VisStimConfig()
    return fullfile(cfg.outputMatFilesFolder, *args)


def VisStimSpreadsheetFile(fileName):
    cfg = VisStimConfig()
    if fileName == "Info_Grating.xlsx":
        return cfg.gratingSpreadsheetFile
    if fileName == "Info_RFmapping.xlsx":
        return cfg.rfSpreadsheetFile
    raise RuntimeError("Set a literal spreadsheet file path for %s in VisStimConfig()." % fileName)


def VisStimStimFile(stimKey):
    cfg = VisStimConfig()
    stimKey = VisStimText(stimKey)
    if stimKey.lower() == "grating":
        return cfg.gratingStimFile
    if stimKey.lower() == "rfmapping":
        return cfg.rfStimFile
    if VisStimIsAbsolute(stimKey):
        return stimKey
    raise RuntimeError("Set cfg.%sStimFile as a literal file path in VisStimConfig()." % stimKey)


def VisStimSettingsFile(*args):
    cfg = VisStimConfig()
    return cfg.settingsFile


def VisStimOneBoxNodeId(settingsFile):
    settingsFile = VisStimText(settingsFile)
    if not os.path.isfile(settingsFile):
        raise RuntimeError("settings.xml not found: %s" % settingsFile)

    txt = open(settingsFile).read()
    processorTags = re.findall(r"<PROCESSOR\b[^>]*>", txt)
    oneBoxTags = [tag for tag in processorTags if 'name="OneBox"' in tag]
    if not oneBoxTags:
        raise RuntimeError("OneBox PROCESSOR node not found in settings.xml: %s" % settingsFile)

    tokens = re.search(r'nodeId="([^"]+)"', oneBoxTags[0])
    if tokens is None:
        raise RuntimeError("OneBox nodeId not found in settings.xml: %s" % settingsFile)
    return tokens.group(1)


def VisStimProbePath(probeLetter, pathType):
    cfg = VisStimConfig()
    if pathType == "kilosort":
        return os.path.dirname(cfg.spikeClustersFile)
    if pathType == "concat_raw":
        return cfg.probeConcatRawFile
    raise RuntimeError("Unknown VisStimProbePath type: %s" % pathType)


def VisStimAdcConcatFile(ksPath):
    cfg = VisStimConfig()
    return cfg.adcConcatRawFile


def VisStimInputFile(fileKey):
    cfg = VisStimConfig()
    if fileKey == "cluster_KSLabel":
        return cfg.clusterKSLabelFile
    if fileKey == "cluster_group":
        return cfg.clusterGroupFile
    if fileKey == "spike_clusters":
        return cfg.spikeClustersFile
    if fileKey == "spike_times":
        return cfg.spikeTimesFile
    if fileKey == "adc_spike_times":
        return cfg.adcSpikeTimesFile
    if fileKey == "probe_concat_raw":
        return cfg.probeConcatRawFile
    if fileKey == "adc_concat_raw":
        return cfg.adcConcatRawFile
    raise RuntimeError("Unknown input file key: %s" % fileKey)


def VisStimRawFile(sessionId, fileKey):
    cfg = VisStimConfig()
    sessionId = VisStimFileStem(sessionId)

    if sessionId == cfg.sessionFolder:
        if fileKey == "settings":
            return cfg.settingsFile
        if fileKey == "adc_continuous":
            return cfg.adcContinuousFile
        if fileKey == "adc_continuous_sample_numbers":
            return cfg.adcContinuousSampleNumbersFile
        if fileKey == "adc_ttl_timestamps":
            return cfg.adcTtlTimestampsFile
        if fileKey == "adc_ttl_sample_numbers":
            return cfg.adcTtlSampleNumbersFile
        if fileKey == "probe_continuous":
            return cfg.probeContinuousFile
        if fileKey == "probe_continuous_sample_numbers":
            return cfg.probeContinuousSampleNumbersFile
        if fileKey == "probe_ttl_timestamps":
            return cfg.probeTtlTimestampsFile
        if fileKey == "probe_ttl_sample_numbers":
            return cfg.probeTtlSampleNumbersFile
        raise RuntimeError("Unknown raw file key: %s" % fileKey)

    for row in cfg.precedingSessionFiles:
        if row[0] == sessionId and row[1] == fileKey:
            return row[2]
    raise RuntimeError("Set the literal file path for %s / %s in cfg.precedingSessionFiles." % (sessionId, fileKey))


def VisStimPrecedingSession(currentSession, precedingSuffix):
    currentSession = VisStimFileStem(currentSession)
    precedingSuffix = VisStimText(precedingSuffix)
    if len(currentSession) >= 8:
        return currentSession[:8] + precedingSuffix
    return precedingSuffix


def VisStimFileStem(pathValue):
    s = VisStimText(pathValue).replace("\\", "/")
    while s != "" and s[-1] == "/":
        s = s[:-1]
    stem = os.path.splitext(os.path.basename(s))[0]
    if stem == "":
        stem = s
    return stem


def VisStimText(value):
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return ""
        value = value[0]
    if value is None:
        return ""
    if isinstance(value, np.ndarray):
        if value.size == 0:
            return ""
        value = value.reshape(-1)[0]
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def VisStimIsAbsolute(pathValue):
    s = VisStimText(pathValue)
    return s != "" and (s[0] == "/" or re.search(r"^[A-Za-z]:[\\/]", s) is not None)


def EnsureParentDir(filePath):
    parentDir = os.path.dirname(filePath)
    EnsureDir(parentDir)


def EnsureDir(dirPath):
    if dirPath != "" and not os.path.isdir(dirPath):
        os.makedirs(dirPath)


def fullfile(*parts):
    return os.path.join(*[str(part) for part in parts])


def readNPY(filename):
    return np.load(filename, allow_pickle=False)


def LoadBinary(filename, nChannels=1, channels=1, precision="int16", frequency=None, start=0, duration=math.inf):
    dtype = np.dtype(precision)
    bytesPerSample = dtype.itemsize
    totalSamples = os.path.getsize(filename) // (nChannels * bytesPerSample)
    if frequency is None:
        firstSample = max(1, math.floor(start) + 1)
        if math.isinf(duration):
            lastSample = totalSamples
        else:
            lastSample = min(totalSamples, firstSample + math.floor(duration) - 1)
    else:
        firstSample = max(1, math.floor(start * frequency) + 1)
        if math.isinf(duration):
            lastSample = totalSamples
        else:
            lastSample = min(totalSamples, firstSample + math.floor(duration * frequency) - 1)

    sampleCount = max(0, lastSample - firstSample + 1)
    if sampleCount == 0:
        return np.array([])

    channels = np.atleast_1d(channels)
    data = np.zeros((sampleCount, len(channels)), dtype=dtype)
    for channelPosition in range(len(channels)):
        channel = int(channels[channelPosition])
        channelData = _load_binary(filename, nChannels, channel - 1, dtype=dtype)
        data[:, channelPosition] = channelData[firstSample - 1:lastSample]
    if len(channels) == 1:
        return data[:, 0]
    return data


def read_tsv_table(path):
    rows = []
    with open(path, newline="") as tableFile:
        reader = csv.DictReader(tableFile, delimiter="\t")
        for row in reader:
            rows.append({key: convert_cell(value) for key, value in row.items()})
    if not rows:
        return {}
    out = {}
    for key in rows[0]:
        out[key] = [row[key] for row in rows]
    return out


def read_spreadsheet_table(path):
    spreadsheetArchive = zipfile.ZipFile(path)
    sharedStrings = []
    if "xl/sharedStrings.xml" in spreadsheetArchive.namelist():
        root = ElementTree.fromstring(spreadsheetArchive.read("xl/sharedStrings.xml"))
        ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for item in root.findall("a:si", ns):
            textParts = [textNode.text or "" for textNode in item.findall(".//a:t", ns)]
            sharedStrings.append("".join(textParts))

    sheetName = "xl/worksheets/sheet1.xml"
    root = ElementTree.fromstring(spreadsheetArchive.read(sheetName))
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    for row in root.findall(".//a:sheetData/a:row", ns):
        values = {}
        for cell in row.findall("a:c", ns):
            cellRef = cell.attrib["r"]
            col = xlsx_col_index(cellRef)
            cellType = cell.attrib.get("t", "")
            cellValue = cell.find("a:v", ns)
            if cellType == "s":
                value = sharedStrings[int(cellValue.text)]
            elif cellType == "inlineStr":
                textParts = [textNode.text or "" for textNode in cell.findall(".//a:t", ns)]
                value = "".join(textParts)
            elif cellValue is None:
                value = ""
            else:
                value = convert_cell(cellValue.text)
            values[col] = value
        maxCol = max(values.keys()) if values else 0
        rows.append([values.get(col, "") for col in range(maxCol + 1)])

    headers = [str(h) for h in rows[0]]
    table = []
    for row in rows[1:]:
        item = {}
        for col, header in enumerate(headers):
            if header != "":
                item[header] = row[col] if col < len(row) else ""
        table.append(item)
    return table


def xlsx_col_index(ref):
    letters = re.match(r"([A-Z]+)", ref).group(1)
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number - 1


def convert_cell(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text == "":
        return ""
    try:
        integerValue = int(text)
        if str(integerValue) == text:
            return integerValue
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def copy_table(table):
    return {key: list(value) for key, value in table.items()}


def load_mat_file(path):
    raw = loadmat(path, squeeze_me=True, struct_as_record=False)
    return {name: mat_to_python(value) for name, value in raw.items() if not name.startswith("__")}


def save_mat_file(path, **variables):
    savemat(path, {name: mat_ready(value) for name, value in variables.items()}, long_field_names=True)


def mat_to_python(value):
    if hasattr(value, "_fieldnames"):
        return SimpleNamespace(**{name: mat_to_python(getattr(value, name)) for name in value._fieldnames})
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            return [mat_to_python(item) for item in value.reshape(-1)]
        return value
    if isinstance(value, np.generic):
        return value.item()
    return value


def mat_ready(value):
    if isinstance(value, SimpleNamespace):
        return {name: mat_ready(item) for name, item in vars(value).items()}
    if isinstance(value, dict):
        return {name: mat_ready(item) for name, item in value.items()}
    if isinstance(value, list):
        out = np.empty((len(value),), dtype=object)
        for index in range(len(value)):
            out[index] = mat_ready(value[index])
        return out
    if value is None:
        return np.array([])
    return value


def field(obj, name):
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)


def get_column(table, name):
    if isinstance(table, dict):
        return table[name]
    if isinstance(table, SimpleNamespace):
        return getattr(table, name)
    if isinstance(table, list):
        return [row[name] for row in table]
    return getattr(table, name)


def length(value):
    if isinstance(value, list):
        return len(value)
    arrayValue = np.asarray(value)
    if arrayValue.ndim == 0:
        return 1
    return arrayValue.shape[0]


def get_trial_field(trials, indices, name):
    if isinstance(trials, list):
        return field(trials[indices], name)
    arrayValue = np.asarray(trials)
    item = arrayValue.reshape(-1)[indices]
    return field(item, name)


def parse_channel_layout(settingsFile):
    txt = open(settingsFile).read()
    channelsLine = re.search(r"<CHANNELS[^>]*>", txt).group(0)
    tokens = re.findall(r'(CH\d+)="([^"]*)"', channelsLine)
    channelNames = [token[0] for token in tokens]
    chids = np.array([int(name.replace("CH", "")) for name in channelNames])

    xpLine = re.search(r"<ELECTRODE_XPOS[^>]*>", txt).group(0)
    tokens = re.findall(r'(CH\d+)="(\d+)"', xpLine)
    xpos = np.array([float(token[1]) for token in tokens])

    ypLine = re.search(r"<ELECTRODE_YPOS[^>]*>", txt).group(0)
    tokens = re.findall(r'(CH\d+)="(\d+)"', ypLine)
    ypos = np.array([float(token[1]) for token in tokens])
    return chids, xpos, ypos


def Sync(spikeTimes, eventTimes, durations=np.array([-1, 1])):
    spikeTimes = np.asarray(spikeTimes).reshape(-1)
    eventTimes = np.asarray(eventTimes).reshape(-1)
    win = np.asarray(durations)
    sync = []
    si = []
    for index in range(len(eventTimes)):
        rel = spikeTimes - eventTimes[index]
        keep = (rel >= win[0]) & (rel <= win[1])
        sync.extend(rel[keep])
        si.extend([index + 1] * int(np.sum(keep)))
    return np.asarray(sync), np.asarray(si)


def SyncHist(sync, si, durations=np.array([-1, 1]), number_of_bins=100, mode="mean"):
    sync = np.asarray(sync).reshape(-1)
    si = np.asarray(si).reshape(-1)
    if len(sync) == 0:
        return np.array([]), np.array([])
    binWidth = (durations[1] - durations[0]) / number_of_bins
    t = np.arange(durations[0], durations[1], binWidth) + binWidth / 2
    keep = (sync >= durations[0]) & (sync < durations[1])
    binIndex = np.floor((sync[keep] - durations[0]) / (durations[1] - durations[0]) * number_of_bins).astype(int)
    counts = np.bincount(binIndex, minlength=number_of_bins)[:number_of_bins]
    trialCount = np.max(si)
    if mode == "mean":
        hist = counts / (trialCount * binWidth)
    elif mode == "sum":
        hist = counts
    else:
        hist = counts / trialCount
    return hist, t


def Threshold(timeValuePairs, comparisonOperator, thresholdValue, **kwargs):
    minimumDuration = kwargs.get("min", 0)
    maximumInterruption = kwargs.get("max", 0)
    timeValuePairs = np.asarray(timeValuePairs)
    timeValues = timeValuePairs[:, 0]
    signalValues = timeValuePairs[:, 1]
    if comparisonOperator == ">":
        thresholdMask = signalValues > thresholdValue
    elif comparisonOperator == ">=":
        thresholdMask = signalValues >= thresholdValue
    elif comparisonOperator == "<=":
        thresholdMask = signalValues <= thresholdValue
    else:
        thresholdMask = signalValues < thresholdValue

    transitions = np.diff(thresholdMask.astype(int))
    startIndices = np.where(transitions == 1)[0]
    endIndices = np.where(transitions == -1)[0]
    if thresholdMask[0]:
        startIndices = np.concatenate([[0], startIndices])
    if thresholdMask[-1]:
        endIndices = np.concatenate([endIndices, [len(thresholdMask) - 1]])

    if len(startIndices) > 1 and len(endIndices) > 0:
        interruptionDurations = timeValues[startIndices[1:]] - timeValues[endIndices[:-1]]
        ignoredInterruptions = np.where(interruptionDurations <= maximumInterruption)[0]
        if len(ignoredInterruptions) != 0:
            startIndices = np.delete(startIndices, ignoredInterruptions + 1)
            endIndices = np.delete(endIndices, ignoredInterruptions)

    periods = np.column_stack([timeValues[startIndices], timeValues[endIndices]])
    periodDurations = periods[:, 1] - periods[:, 0]
    keepPeriods = periodDurations >= minimumDuration
    startIndices = startIndices[keepPeriods]
    endIndices = endIndices[keepPeriods]
    periods = periods[keepPeriods, :]
    inMask = np.zeros(len(thresholdMask), dtype=bool)
    for index in range(len(startIndices)):
        inMask[startIndices[index]:endIndices[index] + 1] = True
    return periods, inMask


def interp1(sourcePoints, sourceValues, queryPoints):
    sourcePoints = np.asarray(sourcePoints).reshape(-1)
    sourceValues = np.asarray(sourceValues).reshape(-1)
    queryPoints = np.asarray(queryPoints)
    queryValues = np.interp(queryPoints, sourcePoints, sourceValues)
    leftMask = queryPoints < sourcePoints[0]
    rightMask = queryPoints > sourcePoints[-1]
    if np.any(leftMask):
        slope = (sourceValues[1] - sourceValues[0]) / (sourcePoints[1] - sourcePoints[0])
        queryValues[leftMask] = sourceValues[0] + slope * (queryPoints[leftMask] - sourcePoints[0])
    if np.any(rightMask):
        slope = (sourceValues[-1] - sourceValues[-2]) / (sourcePoints[-1] - sourcePoints[-2])
        queryValues[rightMask] = sourceValues[-1] + slope * (queryPoints[rightMask] - sourcePoints[-1])
    return queryValues


def fill_short_gaps(logicalMask, maxGapSamples=600):
    filledMask = np.asarray(logicalMask, dtype=bool).copy()
    trueSampleIndices = np.where(filledMask.reshape(-1))[0]
    if len(trueSampleIndices) < 2:
        return filledMask
    gapLengths = np.diff(trueSampleIndices) - 1
    shortGapLocations = np.where((gapLengths > 0) & (gapLengths <= maxGapSamples))[0]
    flatMask = filledMask.reshape(-1)
    for gapLocation in shortGapLocations:
        firstFalseAfterTrue = trueSampleIndices[gapLocation] + 1
        lastFalseBeforeNextTrue = trueSampleIndices[gapLocation + 1] - 1
        if firstFalseAfterTrue <= lastFalseBeforeNextTrue:
            flatMask[firstFalseAfterTrue:lastFalseBeforeNextTrue + 1] = True
    return flatMask.reshape(filledMask.shape)


def gauss_2d_model(p, x, y):
    return p[0] * np.exp(-(((((x - p[1]) * np.cos(p[5]) + (y - p[3]) * np.sin(p[5])) ** 2) / (2 * p[2] ** 2)) + (((-(x - p[1]) * np.sin(p[5]) + (y - p[3]) * np.cos(p[5])) ** 2) / (2 * (p[2] * p[6]) ** 2)))) + p[4]


_PDFS = {}


def PlotProbeConfig(protocol, chid_pos, settingsFile, *args):
    if protocol == "RFmapping":
        probe_plot = plt.subplot(2, 2, 3)
        probe_pos = probe_plot.get_position()
        probe_plot.set_position([probe_pos.x0, probe_pos.y0, probe_pos.width * 0.8, probe_pos.height * 0.6])
    elif protocol == "Grating":
        probe_pos = args[0]
        probe_plot = plt.gcf().add_axes(probe_pos)
    else:
        raise RuntimeError("Unknown protocol: %s" % protocol)

    shank_width = 70
    shank_height = 8000
    tip_height = 600

    chids, xpos, ypos = parse_channel_layout(settingsFile)

    ux = np.unique(xpos)
    dx = np.diff(ux)
    gap_threshold = 100
    split_idx = np.concatenate([[0], np.where(dx > gap_threshold)[0] + 1, [len(ux)]])
    num_shanks = len(split_idx) - 1

    for shankIndex in range(num_shanks):
        idx_start = split_idx[shankIndex]
        idx_end = split_idx[shankIndex + 1]
        shank_x = ux[idx_start:idx_end]
        cx = np.mean(shank_x)

        probe_plot.fill(
            [cx - shank_width / 2, cx + shank_width / 2, cx + shank_width / 2, cx - shank_width / 2],
            [0, 0, shank_height, shank_height],
            color=[0.8, 0.8, 0.8],
            edgecolor=[0.8, 0.8, 0.8],
        )

        probe_plot.fill(
            [cx - shank_width / 2, cx + shank_width / 2, cx],
            [0, 0, -tip_height],
            color=[0.8, 0.8, 0.8],
            edgecolor=[0.8, 0.8, 0.8],
        )

    probe_plot.set_xlim([-50, 900])
    probe_plot.plot(xpos, ypos, ".", color=[0.3, 0.3, 0.3], markersize=0.8)
    probe_plot.plot(xpos[chid_pos - 1], ypos[chid_pos - 1], ".", color=rgb("dodgerblue"), markersize=10)
    pos = probe_plot.get_position()
    probe_plot.set_position([pos.x0 * 2, pos.y0, pos.width * 0.7, pos.height])
    probe_plot.axis("off")


def distinguishable_colors(n):
    M = 40
    r, g, b = np.meshgrid(np.linspace(0, 1, M), np.linspace(0, 1, M), np.linspace(0, 1, M), indexing="ij")
    cand = np.column_stack([r.ravel(order="F"), g.ravel(order="F"), b.ravel(order="F")])

    brightness = np.max(cand, axis=1)
    sat = np.std(cand, axis=1)
    keep = (brightness < 0.9) & (sat > 0.05)
    cand = cand[keep, :]
    if cand.shape[0] < n:
        raise RuntimeError("Not enough vivid colors; reduce n or loosen thresholds.")

    colors = np.zeros((n, 3))
    colors[0, :] = cand[np.random.randint(cand.shape[0]), :]
    for index in range(1, n):
        d = np.sqrt(np.sum((cand[:, None, :] - colors[None, :index, :]) ** 2, axis=2))
        score = np.min(d, axis=1)
        best = np.argmax(score)
        colors[index, :] = cand[best, :]
    return colors


def SavePDF(file_name):
    figures = [plt.figure(number) for number in plt.get_fignums()]
    if os.path.isfile(file_name):
        os.remove(file_name)
    with PdfPages(file_name) as pdf:
        for figure in figures:
            pdf.savefig(figure)


def exportgraphics(figure, file_name, append=False):
    if file_name.lower().endswith(".pdf"):
        if file_name not in _PDFS or not append:
            if file_name in _PDFS:
                _PDFS[file_name].close()
            _PDFS[file_name] = PdfPages(file_name)
        _PDFS[file_name].savefig(figure, bbox_inches="tight", pad_inches=0.02)
    else:
        figure.savefig(file_name)


def close_pdf(file_name):
    if file_name in _PDFS:
        _PDFS[file_name].close()
        del _PDFS[file_name]


def rgb(name):
    name = str(name).lower()
    cols = {
        "navy": [0, 0, 0.5], "darkblue": [0, 0, 0.5], "blue": [0, 0, 1], "dodgerblue": [0.12, 0.56, 1],
        "skyblue": [0.53, 0.81, 0.92], "lightblue": [0.68, 0.85, 0.9], "steelblue": [0.27, 0.51, 0.71],
        "green": [0, 1, 0], "lime": [0, 1, 0], "forestgreen": [0.13, 0.55, 0.13], "limegreen": [0.2, 0.8, 0.2],
        "lightgreen": [0.56, 0.93, 0.56], "mediumseagreen": [0.24, 0.7, 0.44], "springgreen": [0, 1, 0.5],
        "charstreuse": [0.5, 1, 0], "red": [1, 0, 0], "darkred": [0.55, 0, 0], "indianred": [0.8, 0.36, 0.36],
        "lightcoral": [0.94, 0.5, 0.5], "salmon": [0.98, 0.5, 0.45], "tomato": [1, 0.39, 0.28],
        "orange": [1, 0.5, 0], "darkorange": [1, 0.55, 0], "coral": [1, 0.5, 0.31], "orangered": [1, 0.27, 0],
        "yellow": [1, 1, 0], "gold": [1, 0.84, 0], "khaki": [0.94, 0.9, 0.55], "lightyellow": [1, 1, 0.88],
        "purple": [0.5, 0, 0.5], "indigo": [0.29, 0, 0.51], "violet": [0.93, 0.51, 0.93],
        "mediumorchid": [0.73, 0.33, 0.83], "plum": [0.87, 0.63, 0.87], "pink": [1, 0.75, 0.8],
        "hotpink": [1, 0.41, 0.71], "deeppink": [1, 0.08, 0.58], "lightpink": [1, 0.71, 0.76],
        "palevioletred": [0.86, 0.44, 0.58], "brown": [0.65, 0.16, 0.16], "sienna": [0.63, 0.32, 0.18],
        "saddlebrown": [0.55, 0.27, 0.07], "chocolate": [0.82, 0.41, 0.12], "peru": [0.8, 0.52, 0.25],
        "gray": [0.5, 0.5, 0.5], "grey": [0.5, 0.5, 0.5], "lightgray": [0.83, 0.83, 0.83],
        "darkgray": [0.66, 0.66, 0.66], "slategray": [0.44, 0.5, 0.56], "black": [0, 0, 0], "white": [1, 1, 1],
        "teal": [0, 0.5, 0.5], "turquoise": [0, 0.5, 0.5], "mediumturquoise": [0.28, 0.82, 0.8],
        "paleturquoise": [0.69, 0.93, 0.93], "magenta": [1, 0, 1], "fuchsia": [1, 0, 1],
        "orchid": [0.85, 0.44, 0.84], "mediumvioletred": [0.78, 0.08, 0.52], "lilac": [0.78, 0.64, 0.78],
    }
    if name not in cols:
        raise RuntimeError('Color "%s" not found.' % name)
    return cols[name]
