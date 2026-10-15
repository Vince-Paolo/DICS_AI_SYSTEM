(function () {
    const root = document.documentElement;

    function readToken(name) {
        return getComputedStyle(root).getPropertyValue(name).trim();
    }

    function remapColor(value, previousPalette, nextPalette) {
        if (Array.isArray(value)) {
            return value.map(item => remapColor(item, previousPalette, nextPalette));
        }
        if (typeof value !== 'string') return value;

        const rgb = value.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/i);
        const colorIndex = rgb
            ? previousPalette.findIndex(color => {
                const hex = color.slice(1);
                return [0, 2, 4].every((offset, index) =>
                    parseInt(hex.slice(offset, offset + 2), 16) === Number(rgb[index + 1])
                );
            })
            : previousPalette.findIndex(color => color.toLowerCase() === value.toLowerCase());
        if (colorIndex < 0) return value;

        return rgb && rgb[4]
            ? window.dicsChartTheme.translucent(nextPalette[colorIndex], Number(rgb[4]))
            : nextPalette[colorIndex];
    }

    function applyChartTheme() {
        const previousPalette = window.dicsChartTheme?.palette || [];
        const palette = [
            readToken('--chart-series-1'),
            readToken('--chart-series-2'),
            readToken('--chart-series-3'),
            readToken('--chart-series-4'),
            readToken('--chart-series-5'),
            readToken('--chart-series-6'),
        ];
        const text = readToken('--chart-text');
        const grid = readToken('--chart-grid');

        Chart.defaults.color = text;
        Chart.defaults.borderColor = grid;
        Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
        Chart.defaults.plugins.legend.labels.color = text;
        Chart.defaults.scale.grid.color = grid;
        Chart.defaults.scale.ticks.color = text;

        window.dicsChartTheme = {
            palette,
            translucent(color, alpha) {
                const match = color.match(/^#([0-9a-f]{6})$/i);
                if (!match) return color;
                const hex = match[1];
                const channels = [0, 2, 4].map(index => parseInt(hex.slice(index, index + 2), 16));
                return `rgba(${channels.join(', ')}, ${alpha})`;
            },
        };

        if (previousPalette.length) {
            Object.values(Chart.instances).forEach(chart => {
                chart.data.datasets.forEach(dataset => {
                    ['backgroundColor', 'borderColor', 'hoverBackgroundColor', 'hoverBorderColor']
                        .forEach(property => {
                            if (dataset[property]) {
                                dataset[property] = remapColor(
                                    dataset[property],
                                    previousPalette,
                                    palette,
                                );
                            }
                        });
                });
                chart.update('none');
            });
        }
    }

    applyChartTheme();
    window.addEventListener('dics:themechange', function () {
        applyChartTheme();
    });
})();
