import csv
import math
import os
import re
import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree

import numpy as np
from scipy.io import loadmat, savemat

from Utils.load_files import load_binary as _load_binary


def readNPY(filename):
    return np.load(filename, allow_pickle=False)

def LoadBinary(filename, nChannels=1, channels=1, precision="int16", frequency=None, start=0, duration=math.inf):
    dtype = np.dtype(precision)
    bytes_per_sample = dtype.itemsize
    total_samples = os.path.getsize(filename) // (nChannels * bytes_per_sample)
    if frequency is None:
        first_sample = max(1, math.floor(start) + 1)
        if math.isinf(duration):
            last_sample = total_samples
        else:
            last_sample = min(total_samples, first_sample + math.floor(duration) - 1)
    else:
        first_sample = max(1, math.floor(start * frequency) + 1)
        if math.isinf(duration):
            last_sample = total_samples
        else:
            last_sample = min(total_samples, first_sample + math.floor(duration * frequency) - 1)

    sample_count = max(0, last_sample - first_sample + 1)
    if sample_count == 0:
        return np.array([])

    channels = np.atleast_1d(channels)
    data = np.zeros((sample_count, len(channels)), dtype=dtype)
    for channel_position in range(len(channels)):
        channel = int(channels[channel_position])
        channel_data = _load_binary(filename, nChannels, channel - 1, dtype=dtype)
        data[:, channel_position] = channel_data[first_sample - 1:last_sample]
    if len(channels) == 1:
        return data[:, 0]
    return data

def read_tsv_table(path):
    rows = []
    with open(path, newline="") as table_file:
        tsv_reader = csv.DictReader(table_file, delimiter="\t")
        for row in tsv_reader:
            rows.append({column_name: convert_cell(cell_value_node) for column_name, cell_value_node in row.items()})
    out = {}
    for key in rows[0]:
        out[key] = [row[key] for row in rows]
    return out

def read_spreadsheet_table(path):
    spreadsheet_archive = zipfile.ZipFile(path)
    shared_strings = []
    if "xl/sharedStrings.xml" in spreadsheet_archive.namelist():
        xml_root = ElementTree.fromstring(spreadsheet_archive.read("xl/sharedStrings.xml"))
        xml_namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for shared_string_item in xml_root.findall("a:si", xml_namespace):
            text_parts = [text_node.text or "" for text_node in shared_string_item.findall(".//a:t", xml_namespace)]
            shared_strings.append("".join(text_parts))

    sheet_name = "xl/worksheets/sheet1.xml"
    xml_root = ElementTree.fromstring(spreadsheet_archive.read(sheet_name))
    xml_namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    for row in xml_root.findall(".//a:sheetData/a:row", xml_namespace):
        values = {}
        for spreadsheet_cell in row.findall("a:c", xml_namespace):
            cell_reference = spreadsheet_cell.attrib["r"]
            column_index = xlsx_col_index(cell_reference)
            cell_type = spreadsheet_cell.attrib.get("t", "")
            cell_value_node = spreadsheet_cell.find("a:v", xml_namespace)
            if cell_type == "s":
                value = shared_strings[int(cell_value_node.text)]
            elif cell_type == "inlineStr":
                text_parts = [text_node.text or "" for text_node in spreadsheet_cell.findall(".//a:t", xml_namespace)]
                value = "".join(text_parts)
            elif cell_value_node is None:
                value = ""
            else:
                value = convert_cell(cell_value_node.text)
            values[column_index] = value
        max_col = max(values.keys()) if values else 0
        rows.append([values.get(column_index, "") for column_index in range(max_col + 1)])

    headers = [str(h) for h in rows[0]]
    table = []
    for row in rows[1:]:
        item = {}
        for column_index, header in enumerate(headers):
            if header != "":
                item[header] = row[column_index] if column_index < len(row) else ""
        table.append(item)
    return table

def xlsx_col_index(ref):
    cell_reference = ref
    column_letters = re.match(r"([A-Z]+)", cell_reference).group(1)
    column_number = 0
    for column_letter in column_letters:
        column_number = column_number * 26 + ord(column_letter) - ord("A") + 1
    return column_number - 1

def convert_cell(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text == "":
        return ""
    try:
        integer_value = int(text)
        if str(integer_value) == text:
            return integer_value
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
    return {variable_name: mat_to_python(cell_value_node) for variable_name, cell_value_node in raw.items() if not variable_name.startswith("__")}

def save_mat_file(path, **variables):
    savemat(path, {variable_name: mat_ready(cell_value_node) for variable_name, cell_value_node in variables.items()}, long_field_names=True)

def mat_to_python(value):
    if hasattr(value, "_fieldnames"):
        return SimpleNamespace(**{name: mat_to_python(getattr(value, name)) for name in value._fieldnames})
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            out = [mat_to_python(cell_value_node) for cell_value_node in value.reshape(-1)]
            return out
        return value
    if isinstance(value, np.generic):
        return value.item()
    return value

def mat_ready(value):
    if isinstance(value, SimpleNamespace):
        return {variable_name: mat_ready(cell_value_node) for variable_name, cell_value_node in vars(value).items()}
    if isinstance(value, dict):
        return {variable_name: mat_ready(cell_value_node) for variable_name, cell_value_node in value.items()}
    if isinstance(value, list):
        array_value = np.empty((len(value),), dtype=object)
        for list_index in range(len(value)):
            array_value[list_index] = mat_ready(value[list_index])
        return array_value
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
    array_value = np.asarray(value)
    if array_value.ndim == 0:
        return 1
    return array_value.shape[0]

def get_trial_field(trials, indices, name):
    if isinstance(trials, list):
        return field(trials[indices], name)
    array_value = np.asarray(trials)
    item = array_value.reshape(-1)[indices]
    return field(item, name)

def parse_channel_layout(settings_file):
    settings_xml_text = open(settings_file).read()
    channels_line = re.search(r"<CHANNELS[^>]*>", settings_xml_text).group(0)
    channel_tokens = re.findall(r'(CH\d+)="([^"]*)"', channels_line)
    channel_names = [channel_token[0] for channel_token in channel_tokens]
    channel_ids = np.array([int(channel_name.replace("CH", "")) for channel_name in channel_names])

    xp_line = re.search(r"<ELECTRODE_XPOS[^>]*>", settings_xml_text).group(0)
    electrode_x_tokens = re.findall(r'(CH\d+)="(\d+)"', xp_line)
    channel_x_positions = np.array([float(channel_token[1]) for channel_token in electrode_x_tokens])

    yp_line = re.search(r"<ELECTRODE_YPOS[^>]*>", settings_xml_text).group(0)
    electrode_y_tokens = re.findall(r'(CH\d+)="(\d+)"', yp_line)
    channel_y_positions = np.array([float(channel_token[1]) for channel_token in electrode_y_tokens])
    return channel_ids, channel_x_positions, channel_y_positions
