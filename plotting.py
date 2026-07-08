is_norm: bool = False


def plot_1d_rfmap(unitsSpikeCounts, targetList, *, isNormalize: bool = False, isLineplot: bool = False, isHeatmap:bool = False):
    # Validation
    assert isLineplot + isHeatmap == 1

    if isNormalize:
        max_per_unit = unitsSpikeCounts.max(axis=1, keepdims=True)
        max_per_unit[max_per_unit == 0] = 1  # incase divided by 0 later

        unitsSpikeCounts = unitsSpikeCounts / max_per_unit


    n_units, n_x = unitsSpikeCounts.shape
    x_values = np.arange(n_x)

    plt.figure(figsize=(10, 8))

    offset = 1

    yticks_height = []

    for unit_idx, spikeCounts in enumerate(unitsSpikeCounts):
        y = spikeCounts + (n_units - 1 - unit_idx) * offset
        yticks_height.append(np.average(y))
        plt.plot(x_values, y, linewidth=1)

    plt.xlabel("x")

    plt.yticks(
        yticks_height,
        targetList
    )

    plt.ylabel("Unit ID")

    plt.tight_layout()
    plt.show()