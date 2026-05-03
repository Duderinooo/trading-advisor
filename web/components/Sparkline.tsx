type SparkPoint = { v: number };

export default function Sparkline({
  data,
  color = "var(--good)",
  width = 70,
  height = 24,
}: {
  data: SparkPoint[];
  color?: string;
  width?: number;
  height?: number;
}) {
  if (!data || data.length < 2) return null;
  const vals = data.map((d) => d.v);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const path = data
    .map((d, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((d.v - min) / range) * (height - 4) - 2;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = data[data.length - 1];
  const lastX = width;
  const lastY = height - ((last.v - min) / range) * (height - 4) - 2;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      preserveAspectRatio="none"
    >
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx={lastX} cy={lastY} r="1.6" fill={color} />
    </svg>
  );
}
