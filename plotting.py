import matplotlib.pyplot as plt
import numpy as np

is_norm: bool = False


def plot_1d_rfmap(unitsSpikeCounts: np.ndarray, targetList, *, isNormalize: bool = False, isLineplot: bool = False,
                  isHeatmap: bool = False, offset: float = 1.0, xinDeg: bool = False):
    # Validation
    if isLineplot == isHeatmap:
        raise ValueError("Exactly one of isLineplot or isHeatmap must be True.")

    n_units, n_x = unitsSpikeCounts.shape
    x_values = np.linspace(0, 360, n_x, endpoint=False) if xinDeg else np.arange(n_x)
    x_label = "Angle (deg)" if xinDeg else "x"

    if isNormalize:
        min_per_unit = unitsSpikeCounts.min(axis=1, keepdims=True)
        max_per_unit = unitsSpikeCounts.max(axis=1, keepdims=True)
        range_per_unit = max_per_unit - min_per_unit
        unitsSpikeCounts = np.divide(
            unitsSpikeCounts - min_per_unit,
            range_per_unit,
            out=np.zeros_like(unitsSpikeCounts, dtype=float),
            where=range_per_unit != 0,
        )

    fig, ax = plt.subplots(figsize=(10, 8))

    if isLineplot:
        yticks_height = []

        for unit_idx, spikeCounts in enumerate(unitsSpikeCounts):
            y = spikeCounts + (n_units - 1 - unit_idx) * offset
            yticks_height.append(np.average(y))
            ax.plot(x_values, y, linewidth=1)

        ax.set_yticks(yticks_height)
        ax.set_yticklabels(targetList)

    if isHeatmap:
        imshow_kwargs = dict(
            aspect="auto",
            cmap="viridis",
            interpolation="nearest",
        )
        if xinDeg:
            imshow_kwargs["extent"] = [0, 360, n_units - 0.5, -0.5]

        im = ax.imshow(unitsSpikeCounts, **imshow_kwargs)

        ax.set_yticks(np.arange(n_units))
        ax.set_yticklabels(targetList)
        fig.colorbar(im, ax=ax, label="Normalized spikes" if isNormalize else "Spikes")

    ax.set_xlabel(x_label)
    ax.set_ylabel("Unit ID")
    if xinDeg:
        ax.set_xlim(0, 360)
        ax.set_xticks(np.arange(0, 361, 60))

    plt.tight_layout()
    plt.show()
