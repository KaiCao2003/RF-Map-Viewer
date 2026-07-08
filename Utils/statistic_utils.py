import numpy as np
from collections.abc import Mapping


def basic_statistics(target: list | np.ndarray) -> None:
    """Print min, max, mean, median and std of a list or numpy array."""

    if isinstance(target, list):
        target = np.array(target)

    print(f"min: {min(target)}")
    print(f"max: {max(target)}")
    print(f"mean: {np.mean(target)}")
    print(f"median: {np.median(target)}")
    print(f"std: {np.std(target)}")


def select_by_indices(index_dict: dict, data_dict: dict | list | np.ndarray, *, pre_range: int = 0,
                      post_range: int = 0) -> dict:
    """
    index_dict: the 'a' (index) dict (>=2 layers)
    data_dict:  the 'x' (data) dict (exactly one layer shallower than index_dict)

    Behavior:
      - For overlapping layers, keys must match.
      - At the leaf: data_dict has a list; index_dict has a dict of one-or-more keys,
        each mapping to a list of integer indices. Those leaf keys (e.g. 'c', 'c1', 'b2')
        are preserved in the output and each becomes the selected list from data_dict's list.
    """

    def _to_list(seq: list | np.ndarray) -> list:
        if isinstance(seq, np.ndarray):
            return seq.tolist()
        return list(seq)

    def pick_windows(indices_list: list, base_seq: list | np.ndarray) -> list:
        base_list = _to_list(base_seq)
        n = len(base_list)
        out: list = []
        for i in indices_list:
            if not isinstance(i, int):
                out.append(None)
                continue
            if i < 0 or i >= n:
                out.append(None)
                continue
            start = max(0, i - pre_range)
            end = min(n - 1, i + post_range)
            # end is inclusive; Python slice needs end+1
            out.append(base_list[start:end + 1])
        return out

    # Recurse through dict layers
    if isinstance(data_dict, dict):
        # For each shared key, recurse, passing along the ranges
        return {
            k: select_by_indices(index_dict[k], v, pre_range=pre_range, post_range=post_range)
            for k, v in data_dict.items()
        }

    # Leaf: index_dict is a mapping {leaf_name: [indices]}
    result_leaf: dict = {}
    for leaf_name, indices in index_dict.items():
        result_leaf[leaf_name] = pick_windows(indices, data_dict)

    return result_leaf


def _find_innermost_dicts(nested_dict):
    """
    Recursively traverse a nested dictionary and collect all 'innermost' dictionaries.
    An innermost dictionary is one whose values are not dictionaries themselves.
    """
    innermost_dicts = []

    def _traverse(current_value):
        if isinstance(current_value, Mapping):
            # If all values are non-dict, treat as a leaf
            if current_value and all(not isinstance(v, Mapping) for v in current_value.values()):
                innermost_dicts.append(current_value)
            else:
                # Otherwise, keep going deeper
                for sub_value in current_value.values():
                    _traverse(sub_value)

    _traverse(nested_dict)
    return innermost_dicts


def collect_innermost_fields(nested_data, *, fill_value=None):
    """
    Traverse an arbitrarily nested dictionary and aggregate all innermost (leaf-level)
    dictionaries into a combined structure where each key maps to a list of its values
    across all leaves.

    Parameters
    ----------
    nested_data : dict
        The input dictionary that may contain multiple layers of nested dictionaries.
    fill_value : any, optional
        A value to use if a specific key is missing in some innermost dictionaries.

    Returns
    -------
    dict
        A dictionary mapping each leaf key to a list of its collected values.
    """
    innermost_dicts = _find_innermost_dicts(nested_data)
    if not innermost_dicts:
        return {}

    # Collect all keys that appear in any innermost dict
    all_leaf_keys = set().union(*(leaf.keys() for leaf in innermost_dicts))

    # Aggregate values for each key
    aggregated_result = {
        key: [leaf.get(key, fill_value) for leaf in innermost_dicts]
        for key in all_leaf_keys
    }

    return aggregated_result
