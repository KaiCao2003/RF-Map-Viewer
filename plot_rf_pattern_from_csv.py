from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Wedge


CSV_FILE = Path("RFmap_ON_OnSet.csv")
OUTPUT_DIR = Path("RFmap_ON_OnSet_pages_python")


def plot_unit(unit_df, output_dir):
    unit_id = int(unit_df["unit_id"].iloc[0])
    unit_index = int(unit_df["unit_index"].iloc[0])

    rf = (
        unit_df.pivot(index="row", columns="col", values="rf_value")
        .sort_index()
        .sort_index(axis=1)
        .to_numpy()
    )
    vmin = np.nanmin(rf)
    vmax = np.nanmax(rf)
    norm = plt.Normalize(vmin, vmax)
    cmap = plt.cm.gray

    rf_rows = int(unit_df["rf_rows"].iloc[0])
    rf_cols = int(unit_df["rf_cols"].iloc[0])
    polar_radius = float(unit_df["polar_plot_radius"].iloc[0])
    plot_height = 8.0
    left_width = plot_height * rf_cols / rf_rows
    polar_size = 2 * polar_radius * (plot_height / rf_rows)

    fig_width = left_width + polar_size + 4.5
    fig_height = polar_size + 2.8
    fig = plt.figure(figsize=(fig_width / 2.54, fig_height / 2.54))

    left_ax = fig.add_axes(
        [1.1 / fig_width, (1.0 + (polar_size - plot_height) / 2) / fig_height,
         left_width / fig_width, plot_height / fig_height]
    )
    image = left_ax.imshow(rf, origin="upper", cmap=cmap, aspect="equal", vmin=vmin, vmax=vmax)
    left_ax.set_title("ON onset RFmap")
    fig.colorbar(image, ax=left_ax, fraction=0.046)

    polar_ax = fig.add_axes(
        [(left_width + 3.2) / fig_width, 1.0 / fig_height,
         polar_size / fig_width, polar_size / fig_height]
    )
    for _, row in unit_df.iterrows():
        wedge = Wedge(
            (0, 0),
            row["r_outer"],
            row["theta_end_deg"],
            row["theta_start_deg"],
            width=row["r_outer"] - row["r_inner"],
        )
        wedge.set_facecolor(cmap(norm(row["rf_value"])))
        wedge.set_edgecolor("none")
        polar_ax.add_patch(wedge)

    polar_ax.set_xlim(-polar_radius, polar_radius)
    polar_ax.set_ylim(-polar_radius, polar_radius)
    polar_ax.set_aspect("equal")
    polar_ax.axis("off")
    polar_ax.set_title("Polar RFmap")

    fig.suptitle(f"Unit {unit_id}")
    output_file = output_dir / f"{unit_index:03d}_unit_{unit_id}.pdf"
    fig.savefig(output_file, dpi=200)
    plt.close(fig)


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    df = pd.read_csv(CSV_FILE)
    for _, unit_df in df.groupby("unit_index", sort=True):
        plot_unit(unit_df, OUTPUT_DIR)


if __name__ == "__main__":
    main()
