import os

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from Utils.visual_stimuli_io import parse_channel_layout


_PDFS = {}


def PlotProbeConfig(protocol, highlight_channel_position, settings_file, *args):
    if protocol == "RFmapping":
        probe_plot_axis = plt.subplot(2, 2, 3)
        probe_axis_position = probe_plot_axis.get_position()
        probe_plot_axis.set_position([
            probe_axis_position.x0,
            probe_axis_position.y0,
            probe_axis_position.width * 0.8,
            probe_axis_position.height * 0.6,
        ])
    else:
        probe_axis_position = args[0]
        current_figure = plt.gcf()
        probe_plot_axis = current_figure.add_axes(probe_axis_position)

    shank_width = 70
    shank_height = 8000
    tip_height = 600

    channel_ids, channel_x_positions, channel_y_positions = parse_channel_layout(settings_file)

    unique_x_positions = np.unique(channel_x_positions)
    x_position_differences = np.diff(unique_x_positions)
    gap_threshold = 100
    shank_split_indices = np.concatenate([[0], np.where(x_position_differences > gap_threshold)[0] + 1, [len(unique_x_positions)]])
    shank_count = len(shank_split_indices) - 1

    for shank_index in range(shank_count):
        shank_start_index = shank_split_indices[shank_index]
        shank_end_index = shank_split_indices[shank_index + 1]
        shank_x_positions = unique_x_positions[shank_start_index:shank_end_index]
        shank_center_x = np.mean(shank_x_positions)

        probe_plot_axis.fill(
            [shank_center_x - shank_width / 2, shank_center_x + shank_width / 2, shank_center_x + shank_width / 2, shank_center_x - shank_width / 2],
            [0, 0, shank_height, shank_height],
            color=[0.8, 0.8, 0.8],
            edgecolor=[0.8, 0.8, 0.8],
        )
        probe_plot_axis.fill(
            [shank_center_x - shank_width / 2, shank_center_x + shank_width / 2, shank_center_x],
            [0, 0, -tip_height],
            color=[0.8, 0.8, 0.8],
            edgecolor=[0.8, 0.8, 0.8],
        )

    probe_plot_axis.set_xlim([-50, 900])
    probe_plot_axis.plot(channel_x_positions, channel_y_positions, ".", color=[0.3, 0.3, 0.3], markersize=0.8)
    probe_plot_axis.plot(channel_x_positions[highlight_channel_position - 1], channel_y_positions[highlight_channel_position - 1], ".", color=rgb("dodgerblue"), markersize=10)
    probe_axis_position = probe_plot_axis.get_position()
    probe_plot_axis.set_position([
        probe_axis_position.x0 * 2,
        probe_axis_position.y0,
        probe_axis_position.width * 0.7,
        probe_axis_position.height,
    ])
    probe_plot_axis.axis("off")

def distinguishable_colors(number_of_colors):
    color_grid_size = 40
    red_grid, green_grid, blue_grid = np.meshgrid(np.linspace(0, 1, color_grid_size), np.linspace(0, 1, color_grid_size), np.linspace(0, 1, color_grid_size), indexing="ij")
    candidate_colors = np.column_stack([red_grid.ravel(order="F"), green_grid.ravel(order="F"), blue_grid.ravel(order="F")])

    brightness = np.max(candidate_colors, axis=1)
    saturation = np.std(candidate_colors, axis=1)
    valid_color_mask = (brightness < 0.9) & (saturation > 0.05)
    candidate_colors = candidate_colors[valid_color_mask, :]

    colors = np.zeros((number_of_colors, 3))
    colors[0, :] = candidate_colors[np.random.randint(candidate_colors.shape[0]), :]

    for color_index in range(1, number_of_colors):
        distances_to_selected_colors = np.sqrt(np.sum((candidate_colors[:, None, :] - colors[None, :color_index, :]) ** 2, axis=2))
        score = np.min(distances_to_selected_colors, axis=1)
        best_color_index = np.argmax(score)
        colors[color_index, :] = candidate_colors[best_color_index, :]
    return colors

def SavePDF(file_name):
    figures = [plt.figure(figure_number) for figure_number in plt.get_fignums()]
    if os.path.isfile(file_name):
        os.remove(file_name)
    pdf_pages = PdfPages(file_name)
    for figure in figures:
        pdf_pages.savefig(figure)
    pdf_pages.close()

def rgb(name):
    name = str(name).lower()
    color_lookup = {
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
    return color_lookup[name]

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
