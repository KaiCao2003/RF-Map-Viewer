import math
import os
import re
from types import SimpleNamespace

import numpy as np


def VisStimConfig():
    visual_stimuli_config = SimpleNamespace()
    visual_stimuli_config.gratingStimFile = ""

    visual_stimuli_config.sessionFolder = "260603_4"
    visual_stimuli_config.probeLetter = "B"
    visual_stimuli_config.session = visual_stimuli_config.sessionFolder + "_Probe" + visual_stimuli_config.probeLetter
    oneBoxStream = "OneBox-103"

    visual_stimuli_config.gratingSpreadsheetRow = 10
    visual_stimuli_config.rfSpreadsheetRow = 1

    resfile_folder_path = "/mnt/senzailab/"
    recording_date = "260603/"

    data_folder = "/mnt/ssd4.1/260603_4/"
    stimulus_folder = "/mnt/senzailab/Kai/#Recording/m12/260603/260603_4/"

    recording_folder = data_folder + recording_date + "Record Node 102/"
    pipeline_folder = data_folder + "pipeline/" + visual_stimuli_config.sessionFolder + "/Probe" + visual_stimuli_config.probeLetter + "/"
    visual_stimuli_config.rfStimFile = stimulus_folder + "260603.mat"
    visual_stimuli_config.adcConcatRawFile = data_folder + "pipeline/" + visual_stimuli_config.sessionFolder + "/OneBox-ADC/concat/traces_cached_seg0.raw"

    kilosort_folder = pipeline_folder + "kilosort/"
    probe_concat_folder = pipeline_folder + "concat/"

    visual_stimuli_config.outputMatFilesFolder = "/mnt/ssd4.1/MatFiles"

    visual_stimuli_config.gratingSpreadsheetFile = resfile_folder_path + "Kai/MatlabApps/360/Info_Grating.xlsx"
    visual_stimuli_config.rfSpreadsheetFile = resfile_folder_path + "Kai/MatlabApps/360/Info_RFmapping.xlsx"

    visual_stimuli_config.settingsFile = recording_folder + "settings.xml"

    visual_stimuli_config.clusterKSLabelFile = kilosort_folder + "cluster_KSLabel.tsv"
    visual_stimuli_config.clusterGroupFile = kilosort_folder + "cluster_group.tsv"
    visual_stimuli_config.spikeClustersFile = kilosort_folder + "spike_clusters.npy"
    visual_stimuli_config.spikeTimesFile = kilosort_folder + "spike_times.npy"

    visual_stimuli_config.adcSpikeTimesFile = visual_stimuli_config.spikeTimesFile

    visual_stimuli_config.probeConcatRawFile = probe_concat_folder + "traces_cached_seg0.raw"

    visual_stimuli_config.adcContinuousFile = recording_folder + "experiment1/recording1/continuous/" + oneBoxStream + ".OneBox-ADC/continuous.dat"
    visual_stimuli_config.adcContinuousSampleNumbersFile = recording_folder + "experiment1/recording1/continuous/" + oneBoxStream + ".OneBox-ADC/sample_numbers.npy"
    visual_stimuli_config.adcTtlTimestampsFile = recording_folder + "experiment1/recording1/events/" + oneBoxStream + ".OneBox-ADC/TTL/timestamps.npy"
    visual_stimuli_config.adcTtlSampleNumbersFile = recording_folder + "experiment1/recording1/events/" + oneBoxStream + ".OneBox-ADC/TTL/sample_numbers.npy"

    visual_stimuli_config.probeContinuousFile = recording_folder + "experiment1/recording1/continuous/" + oneBoxStream + ".Probe" + visual_stimuli_config.probeLetter + "/continuous.dat"
    visual_stimuli_config.probeContinuousSampleNumbersFile = recording_folder + "experiment1/recording1/continuous/" + oneBoxStream + ".Probe" + visual_stimuli_config.probeLetter + "/sample_numbers.npy"
    visual_stimuli_config.probeTtlTimestampsFile = recording_folder + "experiment1/recording1/events/" + oneBoxStream + ".Probe" + visual_stimuli_config.probeLetter + "/TTL/timestamps.npy"
    visual_stimuli_config.probeTtlSampleNumbersFile = recording_folder + "experiment1/recording1/events/" + oneBoxStream + ".Probe" + visual_stimuli_config.probeLetter + "/TTL/sample_numbers.npy"

    visual_stimuli_config.precedingSessionFiles = []
    visual_stimuli_config.fmaToolboxRoot = resfile_folder_path + "Kai/MatlabApps/buzcode/externalPackages/FMAToolbox"
    return visual_stimuli_config

def VisStimMatFile(*args):
    visual_stimuli_config = VisStimConfig()
    return fullfile(visual_stimuli_config.outputMatFilesFolder, *args)

def VisStimSpreadsheetFile(file_name):
    visual_stimuli_config = VisStimConfig()
    if file_name == "Info_Grating.xlsx":
        return visual_stimuli_config.gratingSpreadsheetFile
    if file_name == "Info_RFmapping.xlsx":
        return visual_stimuli_config.rfSpreadsheetFile
    raise RuntimeError("Set a literal spreadsheet file path for %s in VisStimConfig()." % file_name)

def VisStimStimFile(stimulus_key):
    visual_stimuli_config = VisStimConfig()
    stimulus_key = VisStimText(stimulus_key)
    if stimulus_key.lower() == "grating":
        return visual_stimuli_config.gratingStimFile
    if stimulus_key.lower() == "rfmapping":
        return visual_stimuli_config.rfStimFile
    if VisStimIsAbsolute(stimulus_key):
        return stimulus_key
    raise RuntimeError("Set cfg.%sStimFile as a literal file path in VisStimConfig()." % stimulus_key)

def VisStimSettingsFile(*args):
    visual_stimuli_config = VisStimConfig()
    return visual_stimuli_config.settingsFile

def VisStimOneBoxNodeId(settings_file):
    settings_file = VisStimText(settings_file)
    settings_xml_text = open(settings_file).read()
    processor_tags = re.findall(r"<PROCESSOR\b[^>]*>", settings_xml_text)
    oneBox_processor_tags = [tag for tag in processor_tags if 'name="OneBox"' in tag]
    node_id_match = re.search(r'nodeId="([^"]+)"', oneBox_processor_tags[0])
    return node_id_match.group(1)

def VisStimProbePath(probeLetter, pathType):
    visual_stimuli_config = VisStimConfig()
    path_type = pathType
    if path_type == "kilosort":
        return os.path.dirname(visual_stimuli_config.spikeClustersFile)
    if path_type == "concat_raw":
        return visual_stimuli_config.probeConcatRawFile
    raise RuntimeError("Unknown VisStimProbePath type: %s" % path_type)

def VisStimAdcConcatFile(kilosort_path):
    visual_stimuli_config = VisStimConfig()
    return visual_stimuli_config.adcConcatRawFile

def VisStimInputFile(file_key):
    visual_stimuli_config = VisStimConfig()
    if file_key == "cluster_KSLabel":
        return visual_stimuli_config.clusterKSLabelFile
    if file_key == "cluster_group":
        return visual_stimuli_config.clusterGroupFile
    if file_key == "spike_clusters":
        return visual_stimuli_config.spikeClustersFile
    if file_key == "spike_times":
        return visual_stimuli_config.spikeTimesFile
    if file_key == "adc_spike_times":
        return visual_stimuli_config.adcSpikeTimesFile
    if file_key == "probe_concat_raw":
        return visual_stimuli_config.probeConcatRawFile
    if file_key == "adc_concat_raw":
        return visual_stimuli_config.adcConcatRawFile
    raise RuntimeError("Unknown input file key: %s" % file_key)

def VisStimRawFile(session_id, file_key):
    visual_stimuli_config = VisStimConfig()
    session_id = VisStimFileStem(session_id)

    if session_id == visual_stimuli_config.sessionFolder:
        if file_key == "settings":
            return visual_stimuli_config.settingsFile
        if file_key == "adc_continuous":
            return visual_stimuli_config.adcContinuousFile
        if file_key == "adc_continuous_sample_numbers":
            return visual_stimuli_config.adcContinuousSampleNumbersFile
        if file_key == "adc_ttl_timestamps":
            return visual_stimuli_config.adcTtlTimestampsFile
        if file_key == "adc_ttl_sample_numbers":
            return visual_stimuli_config.adcTtlSampleNumbersFile
        if file_key == "probe_continuous":
            return visual_stimuli_config.probeContinuousFile
        if file_key == "probe_continuous_sample_numbers":
            return visual_stimuli_config.probeContinuousSampleNumbersFile
        if file_key == "probe_ttl_timestamps":
            return visual_stimuli_config.probeTtlTimestampsFile
        if file_key == "probe_ttl_sample_numbers":
            return visual_stimuli_config.probeTtlSampleNumbersFile
        raise RuntimeError("Unknown raw file key: %s" % file_key)

    rows = visual_stimuli_config.precedingSessionFiles
    for row in rows:
        if row[0] == session_id and row[1] == file_key:
            return row[2]
    raise RuntimeError("Set the literal file path for %s / %s in cfg.precedingSessionFiles." % (session_id, file_key))

def VisStimPrecedingSession(current_session, preceding_suffix):
    current_session = VisStimFileStem(current_session)
    preceding_suffix = VisStimText(preceding_suffix)
    if len(current_session) >= 8:
        return current_session[:8] + preceding_suffix
    return preceding_suffix

def VisStimFileStem(path_value):
    path_text = VisStimText(path_value)
    path_text = path_text.replace("\\", "/")
    while path_text != "" and path_text[-1] == "/":
        path_text = path_text[:-1]
    stem = os.path.splitext(os.path.basename(path_text))[0]
    if stem == "":
        stem = path_text
    return stem

def VisStimText(value):
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return ""
        value = value[0]
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, np.ndarray):
        if value.size == 0:
            return ""
        value = value.reshape(-1)[0]
    return str(value).strip()

def VisStimIsAbsolute(path_value):
    path_text = VisStimText(path_value)
    return path_text != "" and (path_text[0] == "/" or re.search(r"^[A-Za-z]:[\\/]", path_text) is not None)

def EnsureParentDir(file_path):
    parent_directory = os.path.dirname(file_path)
    EnsureDir(parent_directory)

def EnsureDir(directory_path):
    if directory_path != "" and not os.path.isdir(directory_path):
        os.makedirs(directory_path)

def fullfile(*parts):
    return os.path.join(*[str(path_part) for path_part in parts])
