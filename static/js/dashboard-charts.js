const COLORS = {
  present: "#2f6b4f",
  late: "#b36b1d",
  absent: "#9a3b2f",
  unmarked: "#8a7d6c",
  navy: "#1d3552",
  gold: "#b8862a",
  ink: "#1f1a14",
  muted: "#6d6254",
  grid: "rgba(196, 165, 116, 0.28)",
};

function readChartData() {
  const node = document.getElementById("dashboard-charts-data");
  if (!node || !window.Chart) return null;
  try {
    return JSON.parse(node.textContent || "{}");
  } catch {
    return null;
  }
}

function drawChart(id, config) {
  const canvas = document.getElementById(id);
  if (!canvas) return;
  const existing = window.Chart.getChart(canvas);
  if (existing) existing.destroy();
  new window.Chart(canvas, config);
}

function doughnut(labels, values, colors) {
  return {
    type: "doughnut",
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { color: COLORS.muted, boxWidth: 12 } },
      },
      cutout: "62%",
    },
  };
}

function bars(labels, datasets, stacked = false) {
  return {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          stacked,
          ticks: { color: COLORS.muted, maxRotation: 0 },
          grid: { color: COLORS.grid },
        },
        y: {
          stacked,
          beginAtZero: true,
          ticks: { color: COLORS.muted, precision: 0 },
          grid: { color: COLORS.grid },
        },
      },
      plugins: {
        legend: { position: "bottom", labels: { color: COLORS.muted, boxWidth: 12 } },
      },
    },
  };
}

function dataset(label, data, color) {
  return { label, data, backgroundColor: color, borderColor: color, borderRadius: 6, maxBarThickness: 28 };
}

const data = readChartData();
if (data) {
  window.Chart.defaults.font.family = '"Outfit", sans-serif';
  window.Chart.defaults.color = COLORS.ink;

  if (data.role === "student") {
    drawChart(
      "chart-mix",
      doughnut(
        ["Present", "Late", "Absent"],
        [data.mix.present, data.mix.late, data.mix.absent],
        [COLORS.present, COLORS.late, COLORS.absent]
      )
    );
    drawChart(
      "chart-trend",
      bars(data.trend.labels, [
        dataset("Present", data.trend.present, COLORS.present),
        dataset("Late", data.trend.late, COLORS.late),
        dataset("Absent", data.trend.absent, COLORS.absent),
      ], true)
    );
    drawChart(
      "chart-work",
      bars(["Copies scanned"], [
        dataset("Classwork", [data.work.classwork], COLORS.navy),
        dataset("Homework", [data.work.homework], COLORS.gold),
      ])
    );
  } else {
    drawChart(
      "chart-classes",
      bars(data.classes.labels, [
        dataset("Present", data.classes.present, COLORS.present),
        dataset("Late", data.classes.late, COLORS.late),
        dataset("Not marked", data.classes.unmarked, COLORS.unmarked),
      ], true)
    );
    drawChart(
      "chart-today",
      doughnut(
        ["Present", "Late", "Absent", "Not marked"],
        [data.today.present, data.today.late, data.today.absent, data.today.unmarked],
        [COLORS.present, COLORS.late, COLORS.absent, COLORS.unmarked]
      )
    );
    drawChart(
      "chart-week",
      bars(data.week.labels, [
        dataset("In school", data.week.in_school, COLORS.present),
        dataset("Absent", data.week.absent, COLORS.absent),
      ])
    );
  }
}
