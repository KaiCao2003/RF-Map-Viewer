import math
import os
import re

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from scipy.optimize import least_squares

from Utils.visual_stimuli_config import (
    EnsureDir,
    EnsureParentDir,
    VisStimAdcConcatFile,
    VisStimConfig,
    VisStimFileStem,
    VisStimInputFile,
    VisStimMatFile,
    VisStimPrecedingSession,
    VisStimProbePath,
    VisStimRawFile,
    VisStimSettingsFile,
    VisStimSpreadsheetFile,
    VisStimStimFile,
    VisStimText,
    fullfile,
)
from Utils.visual_stimuli_io import (
    LoadBinary,
    copy_table,
    field,
    get_column,
    get_trial_field,
    length,
    load_mat_file,
    parse_channel_layout,
    readNPY,
    read_spreadsheet_table,
    read_tsv_table,
    save_mat_file,
)
from Utils.visual_stimuli_plot import PlotProbeConfig, distinguishable_colors, exportgraphics, rgb, close_pdf
from Utils.visual_stimuli_signal import Sync, SyncHist, Threshold, fill_short_gaps, gauss_2d_model, interp1

# Usage:
# import vs
# vs.do_choice14()  # mean waveforms
# vs.do_choice03()  # RF mapping sync and maps

def do_choice14():
    visual_stimuli_config = VisStimConfig()
    session = visual_stimuli_config.session
    settings_file = VisStimSettingsFile()

    good_units_only = True
    load_data_with_prompts = False
    manual_curation = False

    probe_letter = "A"
    if "Probe" in session:
        probe_letter = re.search(r"Probe([A-Za-z])", session).group(1)

    if load_data_with_prompts:
        kilosort_path = input("Please select kilosort folder: ")
        concat_raw_file = input("Please select concat raw file: ")
        channel_count = 384
    else:
        kilosort_path = VisStimProbePath(probe_letter, "kilosort")
        concat_raw_file = VisStimInputFile("probe_concat_raw")
        channel_count = 384

    unit_labels_file = VisStimMatFile("New_Unit_Labels", session + ".mat")
    EnsureParentDir(unit_labels_file)

    if not os.path.isfile(unit_labels_file):
        kilosort_label_file = VisStimInputFile("cluster_KSLabel")
        cluster_group_file = VisStimInputFile("cluster_group")

        kilosort_labels_table = read_tsv_table(kilosort_label_file)
        cluster_group_table = read_tsv_table(cluster_group_file)
        new_labels = copy_table(kilosort_labels_table)

        if manual_curation:
            edited_label_indices = []
            missing_cluster_group_indices = []
            kilosort_cluster_ids = np.asarray(new_labels["cluster_id"])
            cluster_group_ids = np.asarray(cluster_group_table["cluster_id"])
            for cluster_group_id_index in range(len(cluster_group_ids)):
                hit = np.where(kilosort_cluster_ids == cluster_group_ids[cluster_group_id_index])[0]
                if len(hit) == 0:
                    missing_cluster_group_indices.append(cluster_group_id_index)
                else:
                    edited_label_indices.append(int(hit[0]))

            available_cluster_group_indices = [cluster_group_id_index for cluster_group_id_index in range(len(cluster_group_ids)) if cluster_group_id_index not in missing_cluster_group_indices]
            for output_label_index, cluster_group_index in zip(edited_label_indices, available_cluster_group_indices):
                new_labels["KSLabel"][output_label_index] = cluster_group_table["group"][cluster_group_index]

            if len(missing_cluster_group_indices) > 0:
                first_new_id = cluster_group_ids[missing_cluster_group_indices[0]]
                label_insert_position_matches = np.where(kilosort_cluster_ids < first_new_id)[0]
                start = int(label_insert_position_matches[-1] + 1)
                for key in new_labels:
                    labels_to_shift_after_insert = list(new_labels[key][start:])
                    inserts = [cluster_group_table[key][missing_cluster_group_index] for missing_cluster_group_index in missing_cluster_group_indices]
                    new_labels[key] = list(new_labels[key][:start]) + inserts + labels_to_shift_after_insert

        save_mat_file(unit_labels_file, new_labels=new_labels)

    DepthSort_meanWaveForms(kilosort_path, concat_raw_file, session, settings_file, 384, good_units_only, channel_count, 1)

    waveforms_file = VisStimMatFile("SpikeWaveforms", "mean_wfs", session + ".mat")
    loaded = load_mat_file(waveforms_file)
    mean_waveforms = loaded["meanWaveforms"]

    plt.figure()
    selected_indices = range(25)
    subplot_position = 1
    for waveform_plot_index in selected_indices:
        plt.subplot(5, 5, subplot_position)
        channel_index = int(field(mean_waveforms, "ptp_chan_idx")[waveform_plot_index]) - 1
        unit_id = str(int(field(mean_waveforms, "unitIds")[waveform_plot_index]))
        channel_id = str(int(field(mean_waveforms, "chanmap")[channel_index]))
        plt.plot(field(mean_waveforms, "timepts"), field(mean_waveforms, "data")[channel_index, :, waveform_plot_index])
        plt.title("uid: " + unit_id + ", ch: " + channel_id)
        subplot_position += 1

    return "Finished do_choice14"

def do_choice03():
    print("Choice 03: Sync RF mapping data and show summary figures")

    visual_stimuli_config = VisStimConfig()
    spreadsheet_row_number = visual_stimuli_config.rfSpreadsheetRow
    analysis_mode = "mean"
    number_of_bins = 25
    save_figures = True
    save_gaussian_data = True
    use_event_sample_numbers = True
    sync_to_adc = False
    good_units_only = True
    load_data_with_prompts = False

    spreadsheet_rows = read_spreadsheet_table(VisStimSpreadsheetFile("Info_RFmapping.xlsx"))
    row = spreadsheet_rows[spreadsheet_row_number - 1]
    session_folder = visual_stimuli_config.sessionFolder
    gaussian_data_folder = VisStimMatFile("RF_maps", "Gaussian_Data")
    EnsureDir(gaussian_data_folder)
    gaussian_data_file = fullfile(gaussian_data_folder, VisStimFileStem(session_folder) + ".mat")
    gaussian_data_index_file = gaussian_data_file.replace(".mat", "_idx.mat")
    session = visual_stimuli_config.session
    probe_letter = "A"
    if "Probe" in session:
        probe_letter = re.search(r"Probe([A-Za-z])", session).group(1)

    if load_data_with_prompts:
        kilosort_path = input("Please select kilosort folder: ")
        stimulus_path = input("Please select Psychtoolbox file: ")
        settings_file = input("Please select Probe settings file: ")
    else:
        stimulus_path = VisStimStimFile("rfmapping")
        kilosort_path = VisStimProbePath(probe_letter, "kilosort")
        settings_file = VisStimSettingsFile(session_folder)

    sync_pulses_file = VisStimMatFile("RF_maps", "Sync_Pulses", session + ".mat")
    EnsureParentDir(sync_pulses_file)
    preceding_files = row["prec_files"]
    protocol = row["Protocol"]

    if os.path.isfile(sync_pulses_file):
        loaded = load_mat_file(sync_pulses_file)
        stimulus_times = loaded["Vq"]
        trials = load_mat_file(stimulus_path)["trials"]
    else:
        trials, stimulus_times = Sync_Signals(session_folder, kilosort_path, stimulus_path, preceding_files, settings_file, protocol, None, use_event_sample_numbers, sync_to_adc, probe_letter)
        save_mat_file(sync_pulses_file, Vq=stimulus_times)

    if os.path.isfile(gaussian_data_file):
        gaussian_fit_data = load_mat_file(gaussian_data_file)["gauss_2d_data"]
    else:
        gaussian_fit_data = []

    RFmapping_EB(kilosort_path, session, trials, stimulus_times, analysis_mode, number_of_bins, save_figures, save_gaussian_data, gaussian_fit_data, gaussian_data_index_file, settings_file, sync_to_adc, good_units_only)
    return "Finished do_choice03"

def Sync_Signals(current_session_id, kilosort_path, stimulus_path, preceding_files, settings_file, protocol, stimulus_name, use_event_sample_numbers, sync_to_adc, probe_letter):
    plt.close("all")

    probe_sample_rate = 30000
    adc_sample_rate = 30300.5
    probe_channel_count = 385
    adc_channel_count = 12

    stimulus_mat_data = load_mat_file(stimulus_path)
    trials = stimulus_mat_data["trials"]
    stimulus_info = trials

    adc_function_generator_signal = LoadBinary(VisStimRawFile(current_session_id, "adc_continuous"), frequency=adc_sample_rate, nChannels=12, channels=1)
    adc_function_generator_duration = len(adc_function_generator_signal) / adc_sample_rate

    adc_continuous_sample_numbers = readNPY(VisStimRawFile(current_session_id, "adc_continuous_sample_numbers")).astype(float)

    adc_ttl_timestamps = readNPY(VisStimRawFile(current_session_id, "adc_ttl_timestamps"))
    adc_ttl_sample_numbers = readNPY(VisStimRawFile(current_session_id, "adc_ttl_sample_numbers")).astype(float)
    adc_ttl_sample_numbers = adc_ttl_sample_numbers - adc_continuous_sample_numbers[0]
    adc_ttl_times = adc_ttl_sample_numbers / adc_sample_rate

    if protocol not in ["RFmapping360", "Grating360"]:
        photodiode_adc_channel = 2
    else:
        photodiode_adc_channel = 4

    photodiode_signal = LoadBinary(VisStimRawFile(current_session_id, "adc_continuous"), frequency=adc_sample_rate, nChannels=12, channels=photodiode_adc_channel)
    photodiode_duration = len(photodiode_signal) / adc_sample_rate

    if protocol != "RFmapping360":
        probe_function_generator_signal = LoadBinary(VisStimRawFile(current_session_id, "probe_continuous"), frequency=probe_sample_rate, nChannels=probe_channel_count, channels=385)
        probe_function_generator_duration = len(probe_function_generator_signal) / probe_sample_rate

    probe_continuous_sample_numbers = readNPY(VisStimRawFile(current_session_id, "probe_continuous_sample_numbers")).astype(float)

    probe_ttl_timestamps = readNPY(VisStimRawFile(current_session_id, "probe_ttl_timestamps"))
    probe_ttl_sample_numbers = readNPY(VisStimRawFile(current_session_id, "probe_ttl_sample_numbers")).astype(float)
    probe_ttl_sample_numbers = probe_ttl_sample_numbers - probe_continuous_sample_numbers[0]
    probe_ttl_times = probe_ttl_sample_numbers / probe_sample_rate

    preceding_files = VisStimText(preceding_files)
    preceding_file_suffixes = preceding_files.split(",")
    preceding_file_count = len(preceding_file_suffixes)
    if preceding_files == "" or preceding_files.lower() == "nan":
        preceding_file_count = 0

    adc_preceding_durations = np.zeros(preceding_file_count)
    adc_preceding_sample_counts = np.zeros(preceding_file_count)
    probe_preceding_durations = np.zeros(preceding_file_count)
    bytes_per_sample = 2

    for preceding_file_index in range(preceding_file_count):
        file_session_name = preceding_file_suffixes[preceding_file_index]
        preceding_session_id = VisStimPrecedingSession(current_session_id, file_session_name)
        preceding_adc_continuous_file = VisStimRawFile(preceding_session_id, "adc_continuous")
        adc_preceding_sample_count = os.path.getsize(preceding_adc_continuous_file) / (adc_channel_count * bytes_per_sample)
        adc_preceding_sample_counts[preceding_file_index] = adc_preceding_sample_count
        adc_preceding_durations[preceding_file_index] = adc_preceding_sample_count / adc_sample_rate

        preceding_probe_continuous_file = VisStimRawFile(preceding_session_id, "probe_continuous")
        probe_preceding_sample_count = os.path.getsize(preceding_probe_continuous_file) / (probe_channel_count * bytes_per_sample)
        probe_preceding_durations[preceding_file_index] = probe_preceding_sample_count / probe_sample_rate

    d385_start_time = np.sum(probe_preceding_durations)

    if sync_to_adc:
        adc_concat_file = VisStimAdcConcatFile(kilosort_path)
        concatenated_adc_signal = LoadBinary(adc_concat_file, frequency=adc_sample_rate, nChannels=12, channels=photodiode_adc_channel)
        preceding_sample_count = int(np.sum(adc_preceding_sample_counts))
        photodiode_sample_count = len(photodiode_signal)
        window_start_sample = preceding_sample_count
        window_stop_sample = window_start_sample + photodiode_sample_count
        sample_window = np.arange(window_start_sample, window_stop_sample)
        concatenated_photodiode_signal = np.asarray(concatenated_adc_signal[sample_window], dtype=float)
        concatenated_photodiode_times = (sample_window + 1) / adc_sample_rate

    if not use_event_sample_numbers:
        adc_time_seconds = np.arange(1, len(adc_function_generator_signal) + 1) / adc_sample_rate

        plt.figure()
        adc_time_mask = adc_time_seconds <= photodiode_duration
        plt.plot(adc_time_seconds[adc_time_mask], adc_function_generator_signal[adc_time_mask])
        plt.title("FG signal, ADC0")

        adc_pulse_periods, adc_threshold_mask = Threshold(np.column_stack([adc_time_seconds, adc_function_generator_signal.astype(float)]), ">", 10000, min=0.2)
        adc_pulse_periods_vector = adc_pulse_periods.reshape(-1)

        sweep_time = 20
        sweep_count = int(photodiode_duration / sweep_time)
        blocks1 = np.arange(0, photodiode_duration, sweep_time)[:sweep_count]
        blocks2 = np.arange(sweep_time, photodiode_duration + sweep_time, sweep_time)[:sweep_count]

        pulse_counts_per_sweep = np.array([np.sum((adc_pulse_periods[:, 0] > blocks1[n]) & (adc_pulse_periods[:, 0] <= blocks2[n])) for n in range(sweep_count)])
        adc_pulse_widths = adc_pulse_periods[:, 1] - adc_pulse_periods[:, 0]

        block_finish_indices = np.cumsum(pulse_counts_per_sweep)
        block_start_indices = np.concatenate([[0], block_finish_indices[:-1]])

        shortest_pulse_indices = np.array([np.argmin(adc_pulse_widths[int(block_start_indices[n]):int(block_finish_indices[n])]) + int(block_start_indices[n]) for n in range(len(block_start_indices))])
        leading_pulse_indices = shortest_pulse_indices + 1

        for leading_pulse_list_index in range(len(leading_pulse_indices)):
            pulse_index = leading_pulse_indices[leading_pulse_list_index]
            current_pulse_width = adc_pulse_widths[pulse_index]
            while adc_pulse_widths[pulse_index + 1] > current_pulse_width or adc_pulse_widths[pulse_index] < 0.4:
                leading_pulse_indices[leading_pulse_list_index] = pulse_index + 1
                pulse_index += 1
                current_pulse_width = adc_pulse_widths[pulse_index]

        _, first_occurrence = np.unique(leading_pulse_indices, return_index=True)
        leading_pulse_indices = leading_pulse_indices[np.sort(first_occurrence)]

        adc_sweep_start_times = adc_pulse_periods[leading_pulse_indices, 0]
        adc_sweep_end_times = adc_pulse_periods[leading_pulse_indices[1:], 0]
        plt.plot(adc_sweep_start_times, np.ones(len(adc_sweep_start_times)) * 11000, "gx", markersize=12)
    else:
        adc_sweep_start_times = adc_ttl_times

    photodiode_times = np.arange(1, len(photodiode_signal) + 1) / adc_sample_rate

    plt.figure()
    plt.plot(photodiode_times, photodiode_signal)
    plt.title("PD signal, ADC%d" % photodiode_adc_channel)

    if sync_to_adc:
        plt.figure()
        plt.plot(concatenated_photodiode_times, concatenated_photodiode_signal)
        plt.title("cADC PD signal, ADC%d" % photodiode_adc_channel)
        photodiode_times = concatenated_photodiode_times
        photodiode_signal = concatenated_photodiode_signal

    if protocol == "Grating":
        photodiode_marker_level = 14000
        photodiode_periods, photodiode_threshold_mask = Threshold(np.column_stack([photodiode_times, photodiode_signal.astype(float)]), ">", photodiode_marker_level, min=0.01)
        photodiode_periods = photodiode_periods[(np.diff(photodiode_periods, axis=1)[:, 0] >= 1.5)]
        photodiode_periods = photodiode_periods[(np.diff(photodiode_periods, axis=1)[:, 0] <= 2.5)]

        plt.plot(photodiode_periods[:, 0], np.ones(len(photodiode_periods)) * photodiode_marker_level, "gx")
        plt.plot(photodiode_periods[:, 1], np.ones(len(photodiode_periods)) * photodiode_marker_level, "rx")

        photodiode_periods = np.fliplr(photodiode_periods)
        x1 = 3833.96959
        x2 = 4809.77611
        photodiode_periods = np.column_stack([np.concatenate([[x1], photodiode_periods[:, 0]]), np.concatenate([photodiode_periods[:, 1], [x2]])])
        plt.plot(photodiode_periods[:, 0], np.ones(len(photodiode_periods)) * photodiode_marker_level, "bo")
        plt.plot(photodiode_periods[:, 1], np.ones(len(photodiode_periods)) * photodiode_marker_level, "bo")

    elif protocol == "Grating360":
        threshold = 15000
        photodiode_threshold_mask = photodiode_signal < threshold

        first_gap_fill_sample_limit = 100
        second_gap_fill_sample_limit = 550

        plt.figure()
        plt.plot(photodiode_threshold_mask)
        plt.ylim([-0.1, 1.1])
        plt.title("isAboveThresholdMask")

        plt.figure()
        plt.plot(photodiode_times, photodiode_threshold_mask)
        plt.ylim([-0.1, 1.1])
        plt.title("isAboveThresholdMask")

        photodiode_gap_filled = fill_short_gaps(photodiode_threshold_mask, first_gap_fill_sample_limit)

        plt.figure()
        plt.plot(photodiode_gap_filled)
        plt.ylim([-0.1, 1.1])
        plt.title("pd_noGap")

        plt.figure()
        plt.plot(photodiode_times, photodiode_gap_filled)
        plt.ylim([-0.1, 1.1])
        plt.title("pd_noGap")

        photodiode_processed_mask = fill_short_gaps(~photodiode_gap_filled, second_gap_fill_sample_limit)

        plt.figure()
        plt.plot(photodiode_processed_mask)
        plt.ylim([-0.1, 1.1])
        plt.title("PD, processed signal")

        plt.figure()
        plt.plot(photodiode_times, ~photodiode_processed_mask)
        plt.ylim([-0.1, 1.1])
        plt.title("PD, processed signal")

        photodiode_marker_level = 0.9
        photodiode_periods, photodiode_threshold_mask = Threshold(np.column_stack([photodiode_times, (~photodiode_processed_mask).astype(float)]), ">", photodiode_marker_level, min=1)
        photodiode_periods = photodiode_periods[(np.diff(photodiode_periods, axis=1)[:, 0] >= 1.3)]
        photodiode_periods = photodiode_periods[(np.diff(photodiode_periods, axis=1)[:, 0] <= 2.7)]

        plt.plot(photodiode_periods[:, 0], np.ones(len(photodiode_periods)) * photodiode_marker_level, "gx")
        plt.plot(photodiode_periods[:, 1], np.ones(len(photodiode_periods)) * photodiode_marker_level, "rx")
        plt.plot(photodiode_periods[:, 0], np.ones(len(photodiode_periods)) * photodiode_marker_level, "bo")
        plt.plot(photodiode_periods[:, 1], np.ones(len(photodiode_periods)) * photodiode_marker_level, "bo")

    elif protocol == "RFmapping":
        falling_threshold = 14000
        rising_threshold = 2000
        photodiode_falling_periods, _ = Threshold(np.column_stack([photodiode_times, photodiode_signal.astype(float)]), "<", falling_threshold, min=0.07)
        photodiode_rising_periods, _ = Threshold(np.column_stack([photodiode_times, photodiode_signal.astype(float)]), ">", rising_threshold, min=0.07)

        plt.plot(photodiode_falling_periods, np.ones(photodiode_falling_periods.shape) * falling_threshold, "gx")
        plt.plot(photodiode_rising_periods, np.ones(photodiode_rising_periods.shape) * rising_threshold, "rx")

        photodiode_falling_periods = photodiode_falling_periods[(np.diff(photodiode_falling_periods, axis=1)[:, 0] <= 0.15)]
        photodiode_rising_periods = photodiode_rising_periods[(np.diff(photodiode_rising_periods, axis=1)[:, 0] <= 0.15)]

        plt.plot(photodiode_falling_periods[:, 0], np.ones(len(photodiode_falling_periods)) * falling_threshold, "go")
        plt.plot(photodiode_rising_periods[:, 0], np.ones(len(photodiode_rising_periods)) * rising_threshold, "ro")

        added_rising_time = 3802.18362
        photodiode_rising_periods = np.vstack([photodiode_rising_periods, [added_rising_time, np.nan]])

        photodiode_periods = np.column_stack([photodiode_falling_periods[:, 0], photodiode_rising_periods[:, 0]])
        plt.plot(photodiode_periods[:, 0], np.ones(len(photodiode_periods)) * falling_threshold, "bo")
        plt.plot(photodiode_periods[:, 1], np.ones(len(photodiode_periods)) * rising_threshold, "bo")

    elif protocol == "RFmapping360":
        threshold = 12000
        photodiode_threshold_mask = photodiode_signal < threshold

        plt.figure()
        plt.plot(photodiode_threshold_mask)
        plt.ylim([-0.1, 1.1])
        plt.title("isAboveThresholdMask")

        plt.figure()
        plt.plot(photodiode_times, photodiode_threshold_mask)
        plt.ylim([-0.1, 1.1])
        plt.title("isAboveThresholdMask")

        photodiode_gap_filled = fill_short_gaps(photodiode_threshold_mask, 400)

        plt.figure()
        plt.plot(photodiode_times, photodiode_gap_filled)
        plt.ylim([-0.1, 1.1])
        plt.title("pd_noGap")

        photodiode_processed_mask = fill_short_gaps(~photodiode_gap_filled, 1000)
        photodiode_processed_mask = ~photodiode_processed_mask

        plt.figure()
        plt.plot(photodiode_times, photodiode_processed_mask)
        plt.ylim([-0.1, 1.1])
        plt.title("PD, processed signal")

        falling_threshold = 0.9
        rising_threshold = 0.1
        photodiode_falling_periods, _ = Threshold(np.column_stack([photodiode_times, photodiode_processed_mask.astype(float)]), "<", falling_threshold, min=0.06)
        photodiode_rising_periods, _ = Threshold(np.column_stack([photodiode_times, photodiode_processed_mask.astype(float)]), ">", rising_threshold, min=0.06)

        plt.plot(photodiode_falling_periods, np.ones(photodiode_falling_periods.shape) * falling_threshold, "gx")
        plt.plot(photodiode_rising_periods, np.ones(photodiode_rising_periods.shape) * rising_threshold, "rx")

        if "AA007" in current_session_id:
            remove_before_time = 25
            remove_after_time = 3469
            photodiode_rising_periods = photodiode_rising_periods[photodiode_rising_periods[:, 1] > remove_before_time, :]
            photodiode_rising_periods = photodiode_rising_periods[photodiode_rising_periods[:, 0] < remove_after_time, :]
            photodiode_falling_periods = photodiode_falling_periods[(photodiode_falling_periods[:, 0] > remove_before_time) & (photodiode_falling_periods[:, 0] < remove_after_time), :]

        photodiode_rising_periods = photodiode_rising_periods[1:, :]
        photodiode_periods = np.column_stack([photodiode_falling_periods[:, 0], photodiode_rising_periods[:, 0]])

        plt.plot(photodiode_falling_periods[:, 0], np.ones(len(photodiode_falling_periods)) * falling_threshold, "bo")
        plt.plot(photodiode_rising_periods[:, 0], np.ones(len(photodiode_rising_periods)) * rising_threshold, "bo")

    elif protocol == "NaturalScenes":
        falling_threshold = 14000
        rising_threshold = 2000
        photodiode_falling_periods, _ = Threshold(np.column_stack([photodiode_times, photodiode_signal.astype(float)]), "<", falling_threshold, min=0.2)
        photodiode_rising_periods, _ = Threshold(np.column_stack([photodiode_times, photodiode_signal.astype(float)]), ">", rising_threshold, min=0.2)

        plt.plot(photodiode_falling_periods, np.ones(photodiode_falling_periods.shape) * falling_threshold, "gx")
        plt.plot(photodiode_rising_periods, np.ones(photodiode_rising_periods.shape) * rising_threshold, "rx")

        if stimulus_name != "ImageNet":
            photodiode_falling_periods = photodiode_falling_periods[(np.diff(photodiode_falling_periods, axis=1)[:, 0] <= 0.28)]
            photodiode_rising_periods = photodiode_rising_periods[(np.diff(photodiode_rising_periods, axis=1)[:, 0] <= 0.28)]

        plt.plot(photodiode_falling_periods[:, 0], np.ones(len(photodiode_falling_periods)) * falling_threshold, "go")
        plt.plot(photodiode_rising_periods[:, 0], np.ones(len(photodiode_rising_periods)) * rising_threshold, "ro")

        photodiode_falling_periods = photodiode_falling_periods[:, 0]
        photodiode_rising_periods = photodiode_rising_periods[:, 0]
        photodiode_periods = np.column_stack([photodiode_falling_periods, photodiode_rising_periods])
        plt.plot(photodiode_falling_periods, np.ones(len(photodiode_falling_periods)) * falling_threshold, "bo")
        plt.plot(photodiode_rising_periods, np.ones(len(photodiode_rising_periods)) * rising_threshold, "bo")

    elif protocol == "Movies":
        falling_threshold = 15000
        rising_threshold = 2000
        minimum_frame_time = 0.02
        photodiode_falling_periods, _ = Threshold(np.column_stack([photodiode_times, photodiode_signal.astype(float)]), "<", falling_threshold, min=minimum_frame_time)
        photodiode_rising_periods, _ = Threshold(np.column_stack([photodiode_times, photodiode_signal.astype(float)]), ">", rising_threshold, min=minimum_frame_time)

        plt.plot(photodiode_falling_periods, np.ones(photodiode_falling_periods.shape) * falling_threshold, "gx")
        plt.plot(photodiode_rising_periods, np.ones(photodiode_rising_periods.shape) * rising_threshold, "rx")

        photodiode_rising_periods = photodiode_rising_periods[:, 0]
        photodiode_falling_periods = photodiode_falling_periods[:, 0]
        photodiode_rising_periods = np.delete(photodiode_rising_periods, [0, 1])
        photodiode_falling_periods = np.delete(photodiode_falling_periods, 0)

        plt.plot(photodiode_falling_periods, np.ones(len(photodiode_falling_periods)) * falling_threshold, "go")
        plt.plot(photodiode_rising_periods, np.ones(len(photodiode_rising_periods)) * rising_threshold, "ro")
        photodiode_periods = np.column_stack([photodiode_falling_periods, photodiode_rising_periods])

    periods1_vector = photodiode_periods.reshape(-1)
    periods1_vector = periods1_vector[~np.isnan(periods1_vector)]

    if not use_event_sample_numbers:
        probe_time_seconds = np.arange(1, len(probe_function_generator_signal) + 1) / probe_sample_rate

        plt.figure()
        probe_time_mask = probe_time_seconds <= photodiode_duration
        plt.plot(probe_time_seconds[probe_time_mask], probe_function_generator_signal[probe_time_mask])
        plt.ylim([-0.1, 1.1])
        plt.title("FG signal, Probe")

        probe_pulse_periods, probe_threshold_mask = Threshold(np.column_stack([probe_time_seconds, probe_function_generator_signal.astype(float)]), ">", 0.8, min=0.2)
        probe_pulse_periods_vector = probe_pulse_periods.reshape(-1)

        green_marker_values = np.ones(probe_pulse_periods.shape) * 0.9
        red_marker_values = np.ones(probe_pulse_periods.shape) * 0.88
        plt.plot(probe_pulse_periods, green_marker_values, "gx", markersize=12)
        plt.plot(probe_pulse_periods, red_marker_values, "rx", markersize=12)

        sweep_time = 20
        sweep_count = int(photodiode_duration / sweep_time)
        blocks1 = np.arange(0, photodiode_duration, sweep_time)[:sweep_count]
        blocks2 = np.arange(sweep_time, photodiode_duration + sweep_time, sweep_time)[:sweep_count]

        pulse_counts_per_sweep = np.array([np.sum((probe_pulse_periods[:, 0] > blocks1[n]) & (probe_pulse_periods[:, 0] <= blocks2[n])) for n in range(sweep_count)])
        probe_pulse_widths = probe_pulse_periods[:, 1] - probe_pulse_periods[:, 0]

        block_finish_indices = np.cumsum(pulse_counts_per_sweep)
        block_start_indices = np.concatenate([[0], block_finish_indices[:-1]])

        shortest_pulse_indices = np.array([np.argmin(probe_pulse_widths[int(block_start_indices[n]):int(block_finish_indices[n])]) + int(block_start_indices[n]) for n in range(len(block_start_indices))])
        leading_pulse_indices = shortest_pulse_indices + 1
        if leading_pulse_indices[-1] >= len(probe_pulse_periods):
            leading_pulse_indices = leading_pulse_indices[:-1]

        for leading_pulse_list_index in range(len(leading_pulse_indices)):
            pulse_index = leading_pulse_indices[leading_pulse_list_index]
            current_pulse_width = probe_pulse_widths[pulse_index]
            while probe_pulse_widths[pulse_index + 1] > current_pulse_width or probe_pulse_widths[pulse_index] < 0.4:
                leading_pulse_indices[leading_pulse_list_index] = pulse_index + 1
                pulse_index += 1
                current_pulse_width = probe_pulse_widths[pulse_index]

        _, first_occurrence = np.unique(leading_pulse_indices, return_index=True)
        leading_pulse_indices = leading_pulse_indices[np.sort(first_occurrence)]

        probe_sweep_start_times = probe_pulse_periods[leading_pulse_indices, 0]
        probe_sweep_end_times = probe_pulse_periods[leading_pulse_indices[1:], 0]

        green_marker_values = np.ones(len(probe_sweep_start_times)) * 0.9
        red_marker_values = np.ones(len(probe_sweep_end_times)) * 0.88
        plt.plot(probe_sweep_start_times, green_marker_values, "gx", markersize=12)
        plt.plot(probe_sweep_end_times, red_marker_values, "rx", markersize=12)

        pulse_count = min(len(adc_sweep_start_times), len(probe_sweep_start_times))
        adc_sweep_start_times = adc_sweep_start_times[:pulse_count]
        probe_sweep_start_times = probe_sweep_start_times[:pulse_count]
    else:
        probe_sweep_start_times = probe_ttl_times

    if not sync_to_adc:
        stimulus_times = interp1(adc_sweep_start_times, probe_sweep_start_times, periods1_vector)
        stimulus_times = stimulus_times + d385_start_time
    else:
        stimulus_times = periods1_vector

    stimulus_time_periods = np.column_stack([stimulus_times[0:-1:2], stimulus_times[1::2]])
    if protocol in ["Grating", "Grating360"]:
        stimulus_times = stimulus_time_periods

    return stimulus_info, stimulus_times

def RFmapping_EB(kilosort_path, file_session_name, trials, stimulus_times, analysis_mode, number_of_bins, save_figures, save_gaussian_data, gaussian_fit_data, gaussian_data_index_file, settings_file, sync_to_adc, good_units_only):
    plt.close("all")
    save_dir = VisStimMatFile("RF_maps")
    EnsureDir(fullfile(save_dir, "Spike_Data"))
    EnsureDir(fullfile(save_dir, "Summary_Figures"))
    EnsureDir(fullfile(save_dir, "Gaussian_Data"))
    save_data = fullfile(save_dir, "Spike_Data", file_session_name)
    save_pdf = fullfile(save_dir, "Summary_Figures", file_session_name)
    gaussian_data_output_file = fullfile(save_dir, "Gaussian_Data", file_session_name + ".mat")
    mean_waveforms = load_mat_file(VisStimMatFile("SpikeWaveforms", "mean_wfs", file_session_name + ".mat"))["meanWaveforms"]

    channel_ids, channel_x_positions, channel_y_positions = parse_channel_layout(settings_file)
    channel_map = channel_ids

    stimulus_times = np.asarray(stimulus_times).reshape(-1)
    pulse_count = len(stimulus_times)
    last_offset = stimulus_times[-1] + np.mean(np.diff(stimulus_times))
    stimulus_time_periods = np.column_stack([stimulus_times, np.concatenate([stimulus_times[1:], [last_offset]])])

    stimulus_frame_count = length(trials)
    square_positions_x = np.array([get_trial_field(trials, trial_index, "Square_PositionX") for trial_index in range(stimulus_frame_count)])
    square_positions_y = np.array([get_trial_field(trials, trial_index, "Square_PositionY") for trial_index in range(stimulus_frame_count)])
    square_luminance = np.array([get_trial_field(trials, trial_index, "Square_Luminance") for trial_index in range(stimulus_frame_count)])
    square_size_degrees = get_trial_field(trials, 0, "Square_Size")

    probe_sample_rate = 30000
    if sync_to_adc:
        spike_times = readNPY(VisStimInputFile("adc_spike_times"))
    else:
        spike_times = readNPY(VisStimInputFile("spike_times"))
        spike_times = (spike_times + 1).astype(float) / probe_sample_rate

    spike_clusters = readNPY(VisStimInputFile("spike_clusters"))
    all_cluster_ids = np.unique(spike_clusters)

    if "Mouse" in file_session_name:
        labels_session_match = re.search(r"Mouse\d+_\d{8}_\d+to\d+", file_session_name)
        labels_session_name = labels_session_match.group(0)
    else:
        labels_session_name = file_session_name

    labels_file = VisStimMatFile("New_Unit_Labels", labels_session_name + ".mat")
    loaded_labels = load_mat_file(labels_file)
    if "new_labels" in loaded_labels:
        new_labels = loaded_labels["new_labels"]
    else:
        new_labels = read_tsv_table(VisStimInputFile("cluster_KSLabel"))

    if good_units_only:
        good_unit_mask = np.asarray(get_column(new_labels, "KSLabel")) == "good"
        unit_list = np.asarray(get_column(new_labels, "cluster_id"))[good_unit_mask]
    else:
        unit_list = all_cluster_ids
    unit_count = len(unit_list)
    selected_indices = range(unit_count)

    square_size_degrees = square_size_degrees
    x_position_count = len(np.unique(square_positions_x))
    y_position_count = len(np.unique(square_positions_y))
    unique_position_luminance_count = len(np.unique(np.column_stack([square_positions_x, square_positions_y, square_luminance]), axis=0))
    map_aspect_ratio = x_position_count / y_position_count
    repeat_count = stimulus_frame_count / unique_position_luminance_count

    presentation_count_difference = stimulus_frame_count - pulse_count
    if presentation_count_difference == 0:
        print("Number of passes = number of pulses")
    elif presentation_count_difference < 0:
        print("Number of pulses > number of passes")
        stimulus_time_periods = stimulus_time_periods[:stimulus_frame_count, :]
    else:
        repeat_count = math.floor(pulse_count / unique_position_luminance_count)
        stimulus_frame_count = unique_position_luminance_count * repeat_count
        stimulus_time_periods = stimulus_time_periods[:stimulus_frame_count, :]
        square_positions_x = square_positions_x[:stimulus_frame_count]
        square_positions_y = square_positions_y[:stimulus_frame_count]
        square_luminance = square_luminance[:stimulus_frame_count]

    square_size = square_size_degrees

    if analysis_mode == "sum":
        spike_data_file = save_data + "_RFmap_SumOfSpikes.mat"
        summary_pdf_file = save_pdf + "_Maps_SumOfSpikes.pdf"
    else:
        spike_data_file = save_data + "_RFmap_SpikeRate.mat"
        summary_pdf_file = save_pdf + "_Maps_SpikeRate.pdf"

    if os.path.isfile(spike_data_file):
        receptive_field_map = load_mat_file(spike_data_file)["RFmap"]
    else:
        receptive_field_map = []
        for unit_index in range(unit_count):
            receptive_field_map.append({"ON": {"OnSet": np.zeros((y_position_count, x_position_count, number_of_bins))}, "OFF": {"OnSet": np.zeros((y_position_count, x_position_count, number_of_bins))}, "baseline": 0})

        for unit_index in selected_indices:
            unit_id_value = unit_list[unit_index]
            unit_spike_times = spike_times[spike_clusters == unit_id_value]
            synchronized_spike_times, baseline_event_indices = Sync(unit_spike_times, stimulus_time_periods[0, 0], durations=np.array([-5, 0]))
            baseline, _ = SyncHist(synchronized_spike_times, baseline_event_indices, durations=np.array([-5, 0]), number_of_bins=1, mode=analysis_mode)
            if len(baseline) == 0:
                baseline = 0
            else:
                baseline = baseline[0]
            receptive_field_map[unit_index]["baseline"] = baseline

            for x_position_index in range(x_position_count):
                for y_position_index in range(y_position_count):
                    current_x_position = -(x_position_count - 1) / 2 * square_size_degrees + x_position_index * square_size_degrees
                    current_y_position = -(y_position_count - 1) / 2 * square_size_degrees + y_position_index * square_size_degrees
                    on_stimulus_mask = (square_positions_x == current_x_position) & (square_positions_y == current_y_position) & (square_luminance == 1)
                    off_stimulus_mask = (square_positions_x == current_x_position) & (square_positions_y == current_y_position) & (square_luminance == 0)

                    synchronized_spike_times, on_event_indices = Sync(unit_spike_times, stimulus_time_periods[on_stimulus_mask, 0], durations=np.array([0, 0.1]))
                    onset_histogram, _ = SyncHist(synchronized_spike_times, on_event_indices, durations=np.array([0, 0.1]), number_of_bins=number_of_bins, mode=analysis_mode)
                    if len(onset_histogram) != 0:
                        receptive_field_map[unit_index]["ON"]["OnSet"][y_position_index, x_position_index, :] = onset_histogram

                    synchronized_spike_times, off_event_indices = Sync(unit_spike_times, stimulus_time_periods[off_stimulus_mask, 0], durations=np.array([0, 0.1]))
                    offset_histogram, _ = SyncHist(synchronized_spike_times, off_event_indices, durations=np.array([0, 0.1]), number_of_bins=number_of_bins, mode=analysis_mode)
                    if len(offset_histogram) != 0:
                        receptive_field_map[unit_index]["OFF"]["OnSet"][y_position_index, x_position_index, :] = offset_histogram

            print("done %d out of %d" % (unit_index + 1, unit_count))

        save_mat_file(spike_data_file, RFmap=receptive_field_map)

    unique_x_degrees = np.unique(square_positions_x)
    unique_y_degrees = np.unique(square_positions_y)

    if len(gaussian_fit_data) == 0:
        if save_figures and os.path.isfile(summary_pdf_file):
            os.remove(summary_pdf_file)

        gaussian_fit_data = [None] * len(list(selected_indices))
        for unit_index in selected_indices:
            unit_id_value = unit_list[unit_index]
            waveform_unit_index = int(np.where(field(mean_waveforms, "unitIds") == unit_id_value)[0][0])
            peak_channel_index = int(field(mean_waveforms, "ptp_chan_idx")[waveform_unit_index]) - 1
            peak_channel_waveform = field(mean_waveforms, "data")[peak_channel_index, :, waveform_unit_index]
            nearby_channel_indices = np.arange(peak_channel_index - 2, peak_channel_index + 3)
            nearby_channel_indices = nearby_channel_indices[(nearby_channel_indices >= 0) & (nearby_channel_indices < 384)]
            nearby_waveforms = field(mean_waveforms, "data")[nearby_channel_indices, :, waveform_unit_index]

            figure = plt.figure(figsize=(6.7, 6.7))
            unit_receptive_field_map = receptive_field_map[unit_index]
            onset_maps = [field(field(unit_receptive_field_map, "ON"), "OnSet"), field(field(unit_receptive_field_map, "OFF"), "OnSet")]
            titles = ["ON stim RF map", "OFF stim RF map"]
            gaussian_fit_pair = [None, None]
            baseline = field(unit_receptive_field_map, "baseline")
            clims = []

            for stimulus_polarity_index in range(2):
                axis = figure.add_subplot(2, 2, stimulus_polarity_index + 1)
                axis_position = axis.get_position()
                axis.set_position([
                    axis_position.x0,
                    axis_position.y0,
                    axis_position.width,
                    axis_position.width / map_aspect_ratio,
                ])
                if analysis_mode == "sum":
                    receptive_field = np.sum(onset_maps[stimulus_polarity_index], axis=2) - baseline
                else:
                    receptive_field = np.mean(onset_maps[stimulus_polarity_index], axis=2) - baseline
                    max_firing_rate = np.max(receptive_field)
                    min_firing_rate = np.min(receptive_field)
                    if max_firing_rate > 0 and max_firing_rate > abs(min_firing_rate):
                        gaussian_sign = 1
                    else:
                        gaussian_sign = -1

                image_handle = axis.imshow(receptive_field, extent=[unique_x_degrees[0], unique_x_degrees[-1], unique_y_degrees[-1], unique_y_degrees[0]], aspect="auto", cmap="gray")
                colorbar = plt.colorbar(image_handle, ax=axis, fraction=0.03)
                axis.set_box_aspect(1 / map_aspect_ratio)
                axis.axis("off")
                axis.plot([axis.get_xlim()[1], axis.get_xlim()[1] - 30], [axis.get_ylim()[0], axis.get_ylim()[0]], "r-", linewidth=1.2)
                axis.set_title(titles[stimulus_polarity_index])
                if stimulus_polarity_index == 0:
                    axis.text(axis.get_xlim()[1] - 15, axis.get_ylim()[0] + 5, "30°")
                elif analysis_mode == "sum":
                    colorbar.set_label("# spikes")
                elif analysis_mode == "mean":
                    colorbar.set_label("imp/s")
                clims.append(image_handle.get_clim())

                x_grid, y_grid = np.meshgrid(unique_x_degrees, unique_y_degrees)
                fit_coordinates = np.column_stack([x_grid.reshape(-1), y_grid.reshape(-1)])

                gaussian_amplitude = np.max(receptive_field)
                center_x = 0
                sigma_x = 3 * square_size_degrees
                center_y = 0
                baseline_offset = 0
                theta = 0
                sigma_y_to_x_ratio = 1

                initial_parameters = np.array([gaussian_amplitude, center_x, sigma_x, center_y, baseline_offset, theta, sigma_y_to_x_ratio], dtype=float)
                lower_bounds = np.array([0, min(unique_x_degrees) - 2 * square_size_degrees, square_size_degrees, min(unique_y_degrees) - 2 * square_size_degrees, 0, -math.pi / 2, 0.6], dtype=float)
                upper_bounds = np.array([3 * gaussian_amplitude, max(unique_x_degrees) + 2 * square_size_degrees, 0.5 * max(unique_y_degrees), max(unique_y_degrees) + 2 * square_size_degrees, 0.25 * gaussian_amplitude, math.pi / 2, 1.9], dtype=float)

                if gaussian_sign == -1:
                    gaussian_amplitude = np.min(receptive_field)
                    initial_parameters[0] = gaussian_amplitude
                    lower_bounds[[0, 4]] = [3 * gaussian_amplitude, 0.25 * gaussian_amplitude]
                    upper_bounds[[0, 4]] = [0, 0]

                upper_bounds = np.where(upper_bounds <= lower_bounds, lower_bounds + np.finfo(float).eps, upper_bounds)
                initial_parameters = np.minimum(np.maximum(initial_parameters, lower_bounds), upper_bounds)

                def residual(gaussian_parameters):
                    return gauss_2d_model(gaussian_parameters, fit_coordinates[:, 0], fit_coordinates[:, 1]) - receptive_field.reshape(-1)

                fitted_parameters = least_squares(residual, initial_parameters, bounds=(lower_bounds, upper_bounds)).x
                sigma_y = fitted_parameters[6] * fitted_parameters[2]

                fit_text = "k: %0.1f imp/s, σx: %0.1f°, σy: %0.1f°\nΘ: %0.1f°, sign: %d" % (
                    fitted_parameters[0], fitted_parameters[2], sigma_y, np.rad2deg(fitted_parameters[5]), gaussian_sign
                )

                gaussian_fit_pair[stimulus_polarity_index] = {
                    "pfit": np.concatenate([fitted_parameters, [sigma_y]]),
                    "pfit_labels": ["k(imp/s)", "cx(°)", "σx(°)", "cy(°)", "baseline(imp/s)", "theta(rad)", "σy/σx", "σy(°)"],
                    "rf_fit": gauss_2d_model(fitted_parameters, x_grid, y_grid),
                }
                axis.text(0, -0.32, fit_text, transform=axis.transAxes, va="top")

            figure_axes = [figure_axis for figure_axis in figure.axes if len(figure_axis.images) != 0]
            color_maximum = max(color_limits[1] for color_limits in clims)
            color_minimum = min(color_limits[0] for color_limits in clims)
            for axis in figure_axes[:2]:
                axis.images[0].set_clim(color_minimum, color_maximum)

            ellipse_angles = np.linspace(0, 2 * math.pi, 300)
            for stimulus_polarity_index in range(2):
                axis = figure_axes[stimulus_polarity_index]
                fitted_parameters = field(gaussian_fit_pair[stimulus_polarity_index], "pfit")
                ellipse_x = fitted_parameters[2] * np.cos(ellipse_angles)
                ellipse_y = fitted_parameters[7] * np.sin(ellipse_angles)
                theta = fitted_parameters[5]
                rotation_matrix = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
                rotated_ellipse_coordinates = rotation_matrix @ np.vstack([ellipse_x, ellipse_y])
                axis.plot(rotated_ellipse_coordinates[0, :] + fitted_parameters[1], rotated_ellipse_coordinates[1, :] + fitted_parameters[3], "b", linewidth=1)

            figure.suptitle("Unit %d" % unit_id_value)

            waveform_axis = figure.add_subplot(2, 2, 4)
            reference_axis_position = figure_axes[0].get_position()
            waveform_y_offset = 1000
            for nearby_waveform_index in range(len(nearby_channel_indices)):
                if nearby_channel_indices[nearby_waveform_index] == peak_channel_index:
                    waveform_axis.plot(field(mean_waveforms, "timepts"), nearby_waveforms[nearby_waveform_index, :] + nearby_waveform_index * waveform_y_offset, color=rgb("DodgerBlue"))
                else:
                    waveform_axis.plot(field(mean_waveforms, "timepts"), nearby_waveforms[nearby_waveform_index, :] + nearby_waveform_index * waveform_y_offset, color=[0.2, 0.2, 0.2])
            waveform_scale_y_position = waveform_axis.get_ylim()[0] - 1
            waveform_axis.plot([0, 0.001], [waveform_scale_y_position, waveform_scale_y_position], "k-")
            waveform_axis.text(0.00025, waveform_scale_y_position - 2, "1 ms", fontsize=8)
            waveform_axis.axis("off")
            waveform_axis_position = waveform_axis.get_position()
            waveform_axis.set_position([
                waveform_axis_position.x0 * 1.05,
                waveform_axis_position.y0 * 1.5,
                reference_axis_position.width * 0.4,
                reference_axis_position.height,
            ])
            for nearby_channel_label_index in range(len(nearby_channel_indices)):
                waveform_axis.text(1.1 * waveform_axis.get_xlim()[1], nearby_channel_label_index * waveform_y_offset, str(int(channel_map[nearby_channel_indices[nearby_channel_label_index]])))

            PlotProbeConfig("RFmapping", peak_channel_index + 1, settings_file)
            gaussian_fit_data[unit_index] = gaussian_fit_pair

            if save_figures:
                exportgraphics(figure, summary_pdf_file, append=True)
                plt.close(figure)

        if save_gaussian_data:
            save_mat_file(gaussian_data_output_file, gauss_2d_data=gaussian_fit_data)
    else:
        if save_figures and os.path.isfile(summary_pdf_file):
            os.remove(summary_pdf_file)

        for unit_index in selected_indices:
            unit_id_value = unit_list[unit_index]
            waveform_unit_index = int(np.where(field(mean_waveforms, "unitIds") == unit_id_value)[0][0])
            peak_channel_index = int(field(mean_waveforms, "ptp_chan_idx")[waveform_unit_index]) - 1
            nearby_channel_indices = np.arange(peak_channel_index - 2, peak_channel_index + 3)
            nearby_channel_indices = nearby_channel_indices[(nearby_channel_indices >= 0) & (nearby_channel_indices < 384)]
            nearby_waveforms = field(mean_waveforms, "data")[nearby_channel_indices, :, waveform_unit_index]

            figure = plt.figure(figsize=(6.7, 6.7))
            unit_receptive_field_map = receptive_field_map[unit_index]
            onset_maps = [field(field(unit_receptive_field_map, "ON"), "OnSet"), field(field(unit_receptive_field_map, "OFF"), "OnSet")]
            titles = ["ON stim RF map", "OFF stim RF map"]
            gaussian_fit_pair = gaussian_fit_data[unit_index]
            baseline = field(unit_receptive_field_map, "baseline")
            clims = []

            for stimulus_polarity_index in range(2):
                axis = figure.add_subplot(2, 2, stimulus_polarity_index + 1)
                axis_position = axis.get_position()
                axis.set_position([
                    axis_position.x0,
                    axis_position.y0,
                    axis_position.width,
                    axis_position.width / map_aspect_ratio,
                ])
                if analysis_mode == "sum":
                    receptive_field = np.sum(onset_maps[stimulus_polarity_index], axis=2) - baseline
                    gaussian_sign = 1
                else:
                    receptive_field = np.mean(onset_maps[stimulus_polarity_index], axis=2) - baseline
                    max_firing_rate = np.max(receptive_field)
                    min_firing_rate = np.min(receptive_field)
                    if max_firing_rate > 0 and max_firing_rate > abs(min_firing_rate):
                        gaussian_sign = 1
                    else:
                        gaussian_sign = -1

                image_handle = axis.imshow(receptive_field, extent=[unique_x_degrees[0], unique_x_degrees[-1], unique_y_degrees[-1], unique_y_degrees[0]], aspect="auto", cmap="gray")
                colorbar = plt.colorbar(image_handle, ax=axis, fraction=0.03)
                axis.set_box_aspect(1 / map_aspect_ratio)
                axis.axis("off")
                axis.plot([axis.get_xlim()[1], axis.get_xlim()[1] - 30], [axis.get_ylim()[0], axis.get_ylim()[0]], "r-", linewidth=1.2)
                axis.set_title(titles[stimulus_polarity_index])
                if stimulus_polarity_index == 0:
                    axis.text(axis.get_xlim()[1] - 15, axis.get_ylim()[0] + 5, "30°")
                elif analysis_mode == "sum":
                    colorbar.set_label("# spikes")
                elif analysis_mode == "mean":
                    colorbar.set_label("imp/s")
                clims.append(image_handle.get_clim())

                fitted_parameters = np.asarray(field(gaussian_fit_pair[stimulus_polarity_index], "pfit"), dtype=float).reshape(-1)
                fit_text = "k: %0.1f imp/s, σx: %0.1f°, σy: %0.1f°\nΘ: %0.1f°, sign: %d" % (
                    fitted_parameters[0], fitted_parameters[2], fitted_parameters[7], np.rad2deg(fitted_parameters[5]), gaussian_sign
                )
                axis.text(0, -0.32, fit_text, transform=axis.transAxes, va="top")

            figure_axes = [figure_axis for figure_axis in figure.axes if len(figure_axis.images) != 0]
            color_maximum = max(color_limits[1] for color_limits in clims)
            color_minimum = min(color_limits[0] for color_limits in clims)
            for axis in figure_axes[:2]:
                axis.images[0].set_clim(color_minimum, color_maximum)

            ellipse_angles = np.linspace(0, 2 * math.pi, 300)
            for stimulus_polarity_index in range(2):
                axis = figure_axes[stimulus_polarity_index]
                fitted_parameters = np.asarray(field(gaussian_fit_pair[stimulus_polarity_index], "pfit"), dtype=float).reshape(-1)
                ellipse_x = fitted_parameters[2] * np.cos(ellipse_angles)
                ellipse_y = fitted_parameters[7] * np.sin(ellipse_angles)
                theta = fitted_parameters[5]
                rotation_matrix = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
                rotated_ellipse_coordinates = rotation_matrix @ np.vstack([ellipse_x, ellipse_y])
                axis.plot(rotated_ellipse_coordinates[0, :] + fitted_parameters[1], rotated_ellipse_coordinates[1, :] + fitted_parameters[3], "b", linewidth=1)

            figure.suptitle("Unit %d" % unit_id_value)

            waveform_axis = figure.add_subplot(2, 2, 4)
            reference_axis_position = figure_axes[0].get_position()
            waveform_y_offset = 1000
            for nearby_waveform_index in range(len(nearby_channel_indices)):
                if nearby_channel_indices[nearby_waveform_index] == peak_channel_index:
                    waveform_axis.plot(field(mean_waveforms, "timepts"), nearby_waveforms[nearby_waveform_index, :] + nearby_waveform_index * waveform_y_offset, color=rgb("DodgerBlue"))
                else:
                    waveform_axis.plot(field(mean_waveforms, "timepts"), nearby_waveforms[nearby_waveform_index, :] + nearby_waveform_index * waveform_y_offset, color=[0.2, 0.2, 0.2])
            waveform_scale_y_position = waveform_axis.get_ylim()[0] - 1
            waveform_axis.plot([0, 0.001], [waveform_scale_y_position, waveform_scale_y_position], "k-")
            waveform_axis.text(0.00025, waveform_scale_y_position - 2, "1 ms", fontsize=8)
            waveform_axis.axis("off")
            waveform_axis_position = waveform_axis.get_position()
            waveform_axis.set_position([
                waveform_axis_position.x0 * 1.05,
                waveform_axis_position.y0 * 1.5,
                reference_axis_position.width * 0.4,
                reference_axis_position.height,
            ])
            for nearby_channel_label_index in range(len(nearby_channel_indices)):
                waveform_axis.text(1.1 * waveform_axis.get_xlim()[1], nearby_channel_label_index * waveform_y_offset, str(int(channel_map[nearby_channel_indices[nearby_channel_label_index]])))

            PlotProbeConfig("RFmapping", peak_channel_index + 1, settings_file)

            if save_figures:
                exportgraphics(figure, summary_pdf_file, append=True)
                plt.close(figure)

    figure = plt.figure(figsize=(6.7, 6.7))
    colors = distinguishable_colors(len(list(selected_indices)))
    handles = [None] * len(gaussian_fit_data)
    labels = [0] * len(gaussian_fit_data)
    summary_axes = []
    titles = ["ON stimulus", "OFF stimulus"]
    for stimulus_polarity_index in range(2):
        axis = figure.add_subplot(2, 2, stimulus_polarity_index + 1)
        axis_position = axis.get_position()
        axis.set_position([
            axis_position.x0,
            axis_position.y0,
            axis_position.width,
            axis_position.width / map_aspect_ratio,
        ])
        summary_axes.append(axis)
        axis.set_xlim([unique_x_degrees[0], unique_x_degrees[-1]])
        axis.set_ylim([unique_y_degrees[0], unique_y_degrees[-1]])
        axis.set_box_aspect(1 / map_aspect_ratio)
        for gaussian_fit_index in range(len(gaussian_fit_data)):
            fitted_parameters = field(gaussian_fit_data[gaussian_fit_index][stimulus_polarity_index], "pfit")
            ellipse_angles = np.linspace(0, 2 * math.pi, 300)
            ellipse_x = fitted_parameters[2] * np.cos(ellipse_angles)
            ellipse_y = fitted_parameters[7] * np.sin(ellipse_angles)
            theta = fitted_parameters[5]
            rotation_matrix = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
            rotated_ellipse_coordinates = rotation_matrix @ np.vstack([ellipse_x, ellipse_y])
            legend_line, = axis.plot(rotated_ellipse_coordinates[0, :] + fitted_parameters[1], -(rotated_ellipse_coordinates[1, :] + fitted_parameters[3]), color=colors[gaussian_fit_index, :], linewidth=1)
            handles[gaussian_fit_index] = legend_line
            labels[gaussian_fit_index] = gaussian_fit_index + 1
        axis.set_xlim([unique_x_degrees[0], unique_x_degrees[-1]])
        axis.set_ylim([unique_y_degrees[0], unique_y_degrees[-1]])
        axis.set_title(titles[stimulus_polarity_index])

    valid_label_indices = [label_index for label_index in range(len(labels)) if labels[label_index] != 0]
    summary_axes[-1].legend([handles[label_index] for label_index in valid_label_indices], [str(labels[label_index]) for label_index in valid_label_indices], loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=4)
    summary_axes[0].set_xlabel("deg")
    summary_axes[0].set_ylabel("deg")

    if save_figures:
        exportgraphics(figure, summary_pdf_file, append=True)
        close_pdf(summary_pdf_file)
        plt.close(figure)

def DepthSort_meanWaveForms(kilosort_path, concat_raw_file, session, settings_file, electrode, good_units_only, channel_count, *args):
    samples_before_spike = 60
    samples_after_spike = 60
    target_spike_sample_count = 1000
    total_channel_count = 384
    probe_sample_rate = 30000

    waveform_sample_count = samples_before_spike + samples_after_spike
    waveform_time_ms = (np.arange(1, waveform_sample_count + 1) / probe_sample_rate) * 1000

    if len(args) != 0:
        electrode_dimensions = np.asarray(args[0]).reshape(-1)
    else:
        electrode_dimensions = np.zeros(np.size(electrode))

    electrode = np.asarray(electrode).reshape(-1)
    if len(electrode) > 1:
        for electrode_index in range(len(electrode)):
            DepthSort_meanWaveForms(kilosort_path, concat_raw_file, session, settings_file, electrode[electrode_index], good_units_only, channel_count, electrode_dimensions[electrode_index])
    else:
        print("Sorting electrode %i of %s" % (electrode[0], session))

        spike_cluster_ids = readNPY(VisStimInputFile("spike_clusters"))
        spike_sample_times = readNPY(VisStimInputFile("spike_times")) + 1
        raw_sample_count = os.path.getsize(concat_raw_file) // 2

        labels_file = VisStimMatFile("New_Unit_Labels", session + ".mat")
        loaded_labels = load_mat_file(labels_file)
        if "new_labels" in loaded_labels:
            new_labels = loaded_labels["new_labels"]
        else:
            new_labels = read_tsv_table(VisStimInputFile("cluster_KSLabel"))

        if good_units_only:
            good_unit_mask = np.asarray(get_column(new_labels, "KSLabel")) == "good"
            unit_list = np.asarray(get_column(new_labels, "cluster_id"))[good_unit_mask]
        else:
            unit_list = np.unique(spike_cluster_ids)
        unit_count = len(unit_list)

        channel_ids, channel_x_positions, channel_y_positions = parse_channel_layout(settings_file)
        channel_map = channel_ids.astype(int)

        minimum_channel_indices = np.zeros(unit_count)
        peak_to_peak_channel_indices = np.zeros(unit_count)
        spike_amplitude_profile = np.zeros((total_channel_count, unit_count))

        mean_waveforms_data = np.zeros((total_channel_count, samples_after_spike + samples_before_spike, unit_count))
        with open(concat_raw_file, "rb") as raw_file_handle:
            for unit_index in range(unit_count):
                cluster_spike_indices = np.where(spike_cluster_ids == unit_list[unit_index])[0]
                cluster_spike_count = np.sum(spike_cluster_ids == unit_list[unit_index])
                sample_step = math.floor(cluster_spike_count / target_spike_sample_count)
                if sample_step > 0:
                    sampled_spike_offsets = np.arange(0, sample_step * target_spike_sample_count, sample_step)
                    sampled_spike_indices = cluster_spike_indices[sampled_spike_offsets]
                else:
                    sampled_spike_indices = cluster_spike_indices

                if len(sampled_spike_indices) != 0:
                    unit_waveforms = np.zeros((total_channel_count, samples_after_spike + samples_before_spike, len(sampled_spike_indices)))
                    for sampled_spike_index in range(len(sampled_spike_indices)):
                        spike_array_index = sampled_spike_indices[sampled_spike_index]
                        spike_sample_time = spike_sample_times[spike_array_index]

                        start_raw_index = (float(spike_sample_time) - samples_before_spike) * channel_count + 1
                        end_raw_index = (float(spike_sample_time) + samples_after_spike) * channel_count

                        if start_raw_index < 1 or end_raw_index > raw_sample_count:
                            print("Index exceeds the number of array elements: ii %d, jj %d" % (unit_index + 1, sampled_spike_index + 1))
                            continue

                        sample_count_to_read = int(end_raw_index - start_raw_index + 1)
                        raw_file_handle.seek(int((start_raw_index - 1) * 2), os.SEEK_SET)
                        raw_waveform_values = np.fromfile(raw_file_handle, dtype=np.int16, count=sample_count_to_read)
                        if len(raw_waveform_values) != sample_count_to_read:
                            print("Could not read full waveform: ii %d, jj %d" % (unit_index + 1, sampled_spike_index + 1))
                            continue

                        spike_waveforms = np.reshape(raw_waveform_values, (channel_count, -1), order="F")
                        spike_waveforms = spike_waveforms[channel_map, :]
                        sorted_spike_waveforms = np.sort(spike_waveforms, axis=1).astype(float)
                        lower_median_values = sorted_spike_waveforms[:, waveform_sample_count // 2 - 1:waveform_sample_count // 2]
                        upper_median_values = sorted_spike_waveforms[:, waveform_sample_count // 2:waveform_sample_count // 2 + 1]
                        spike_waveform_medians = (lower_median_values + upper_median_values) / 2
                        spike_waveform_medians = np.sign(spike_waveform_medians) * np.floor(np.abs(spike_waveform_medians) + 0.5)
                        zero_upper_median_mask = (spike_waveform_medians < 0) & (upper_median_values == 0)
                        spike_waveform_medians[zero_upper_median_mask] = np.ceil((lower_median_values[zero_upper_median_mask] + upper_median_values[zero_upper_median_mask]) / 2)
                        spike_waveforms = np.clip(spike_waveforms.astype(np.int32) - spike_waveform_medians.astype(np.int32), -32768, 32767).astype(float)
                        unit_waveforms[:, :, sampled_spike_index] = spike_waveforms

                    mean_waveform = np.squeeze(np.mean(unit_waveforms, axis=2))
                    mean_waveforms_data[:, :, unit_index] = mean_waveform

                    peak_to_peak_amplitudes = np.max(mean_waveform, axis=1) - np.min(mean_waveform, axis=1)
                    peak_channel_index = int(np.argmax(peak_to_peak_amplitudes))
                    peak_to_peak_channel_indices[unit_index] = peak_channel_index + 1

                print("%d out of %d units" % (unit_index + 1, unit_count))

        mean_waveforms = {
            "data": mean_waveforms_data,
            "samplenum": target_spike_sample_count,
            "sbefore": samples_before_spike,
            "safter": samples_after_spike,
            "unitIds": unit_list,
            "chanmap": channel_map,
            "min_chan_idx": minimum_channel_indices,
            "ptp_chan_idx": peak_to_peak_channel_indices,
            "timepts": waveform_time_ms,
            "xpos": channel_x_positions,
            "ypos": channel_y_positions,
        }

        waveforms_output_file = VisStimMatFile("SpikeWaveforms", "mean_wfs", session + ".mat")
        EnsureParentDir(waveforms_output_file)
        save_mat_file(waveforms_output_file, meanWaveforms=mean_waveforms)
