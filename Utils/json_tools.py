# Import necessary libraries
import json  # For working with JSON data
from datetime import datetime  # For handling dates and times
from pathlib import Path  # For object-oriented filesystem paths
from numpy import ndarray

"""
This module provides tools for reading and writing formatted JSON files.

Usage:
    write_formatted_json(name_list, content_list, filename, add_timestamp, path)
    name_list: list of strings - Keys for the JSON object.
    content_list: list of lists - Values for the JSON object.
    filename: string - The desired filename without the .json extension. 
              Defaults to the current date and time in YYYYMMDDHHMM format.
    add_timestamp: boolean - If True, a timestamp is added to the filename. Defaults to False.
    path: string - The directory path where the file will be saved.
    return: None

    read_formatted_json(filepath)
    filepath: string - The full path to the JSON file.
    return: dict - The content of the JSON file as a dictionary.
"""


def is_json_serializable(x):
    try:
        json.dumps(x)
        return True
    except (TypeError, OverflowError):
        return False


def write_formatted_json(
        name_list: list[str],
        content_list: list,
        filename: str = datetime.now().strftime("%Y%m%d%H%M"),
        add_timestamp: bool = False,
        path: str | Path = "",
):
    # Ensure that the key and value lists are of the same length
    if len(name_list) != len(content_list):
        raise ValueError("Input lists must have the same length.")

    path = Path(path) if isinstance(path, str) else path
    out_dir = path if path else Path(".")

    if not out_dir.exists():
        print(f"make dir {out_dir} as it does not exist yet")
        out_dir.mkdir(parents=True, exist_ok=True)

    # Add a timestamp to the filename if requested
    if add_timestamp:
        filename = f"{filename}_{datetime.now().strftime('%Y%m%d%H%M')}"

    # Create a dictionary by zipping the name and content lists
    content = dict(zip(name_list, content_list))

    # Construct the full output file path
    out_file = out_dir / f"{filename}.json"

    # Write the dictionary to the JSON file with an indent of 2 for readability
    with open(out_file, "w") as f:
        json.dump(content, f, indent=2, default=str, ensure_ascii=False)


def read_formatted_json(filepath: str | Path) -> dict:
    """
    Reads a JSON file and returns its content as a dictionary.

    Args:
        filepath (str): The path to the JSON file to be read.

    Returns:
        dict: The content of the JSON file.

    Raises:
        FileNotFoundError: If the specified file does not exist.
    """
    # Create a Path object from the filepath string
    in_file = Path(filepath)

    # Check if the file exists
    if not in_file.exists():
        raise FileNotFoundError(f"File not found: {in_file}")

    # Open and read the JSON file
    with open(in_file, "r") as f:
        content = json.load(f)

    return content


def combine_json_files(path1, path2, output_path):
    """
    Combine two JSON files by concatenating lists under the same keys.

    Args:
        path1 (str): Path to first JSON file
        path2 (str): Path to second JSON file
        output_path (str): Path where combined JSON will be saved
    """
    # Load the two JSON files
    with open(path1, 'r') as f1:
        data1 = json.load(f1)

    with open(path2, 'r') as f2:
        data2 = json.load(f2)

    # Combine the data
    combined = _combine_dicts(data1, data2)

    # Save to output path
    with open(output_path, 'w') as f:
        json.dump(combined, f, indent=2)

    print(f"Successfully combined {path1} and {path2} into {output_path}")


def _combine_dicts(dict1, dict2):
    """
    Recursively combine two dictionaries by concatenating lists.
    """
    combined = {}

    # Get all unique keys from both dictionaries
    all_keys = set(dict1.keys()) | set(dict2.keys())

    for key in all_keys:
        val1 = dict1.get(key)
        val2 = dict2.get(key)

        # If key exists in both
        if val1 is not None and val2 is not None:
            if isinstance(val1, list) and isinstance(val2, list):
                # Concatenate lists
                combined[key] = val1 + val2
            elif isinstance(val1, dict) and isinstance(val2, dict):
                # Recursively combine nested dictionaries
                combined[key] = _combine_dicts(val1, val2)
            else:
                # For other types, keep value from first file
                combined[key] = val1
        # If key exists only in one
        elif val1 is not None:
            combined[key] = val1
        else:
            combined[key] = val2

    return combined
