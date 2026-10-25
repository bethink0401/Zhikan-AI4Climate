import type { EChartsOption } from 'echarts'
import type {
  ActiveVisualization,
  VisualizationMetricData,
  VisualizationMetricValue
} from '@/types/aiChat'

/** Hide retired implementation notes in restored chart messages as well. */
export function visualizationAnswerText(text: string): string {
  return text
    .replace(/数据来自当前可信业务分析结果[。.]?/g, '')
    .replace(/(?:\s*注意[：:]\s*)?不同物理量已通过\s*grouping\.y_axis(?:_index)?_by_metric\s*显式分轴[。.]?/g, '')
    .trim()
}

type SeriesItem = {
  name: string
  type: 'line' | 'bar' | 'pie' | 'radar'
  data?: Array<number | null | { name: string; value: number | null }>
  yAxisIndex?: number
  areaStyle?: Record<string, never>
  barMaxWidth?: number
  itemStyle?: { opacity?: number; borderRadius?: number[] }
  connectNulls?: boolean
  smooth?: boolean
  emphasis?: { focus: string }
}

const METRIC_DISPLAY_NAMES: Record<string, string> = {
  'meteorology.hourly.wind_speed': '风速',
  'meteorology.hourly.wind_direction': '风向',
  'meteorology.hourly.temperature': '温度',
  'meteorology.hourly.humidity': '湿度',
  'meteorology.hourly.precipitation': '降水',
  'meteorology.hourly.cloud_cover': '云量',
  'air_quality.pollutants.no2': '二氧化氮',
  'air_quality.pollutants.nox': '氮氧化物',
  'air_quality.pollutants.pm10': 'PM10',
  'air_quality.pollutants.pm25': 'PM2.5',
  'air_quality.pollutants.o3': '臭氧',
  'air_quality.pollutants.so2': '二氧化硫',
  'air_quality.pollutants.co': '一氧化碳',
  'air_quality.hourly.no2': '二氧化氮',
  'air_quality.hourly.pm25': 'PM2.5',
  'air_quality.hourly.aqhi': '空气质量健康指数',
  'air_quality.aqhi_average': '平均空气质量健康指数',
  'air_quality.pollutant_profile': '污染物时段统计',
  'traffic.vehicle_composition': '车型结构',
  'traffic.pressure_score': '交通压力筛查评分',
  'source.screening_scores': '污染来源筛查评分',
  'spatial.affected_area': '潜在影响区域',
  'spatial.upwind_roads': '上风向道路',
  'spatial.sensitive_pois': '敏感目标',
  'spatial.pollution_path': '条件性传播路径',
  'traffic.vehicle_mix.private_car': '私家车',
  'traffic.vehicle_mix.taxi': '的士',
  'traffic.vehicle_mix.bus': '巴士',
  'traffic.vehicle_mix.light_goods_vehicle': '轻型货车',
  'traffic.vehicle_mix.heavy_goods_vehicle': '重型货车',
  'source.score.traffic': '交通源',
  'source.score.regional_transport': '区域输送',
  'source.score.local_accumulation': '局地积聚',
  'source.score.photochemical': '光化学污染',
  'travel.departure.intake_index': '出发时段吸入当量',
  'travel.routes.intake_index': '路线吸入当量',
  'travel.routes.duration_minutes': '路线所需时间',
  'travel.routes.score': '路线综合评分',
  'travel.uncertainty.top_pick_share': '推荐稳健性',
  'travel.conditions.severity': '出行条件影响因素',
  'weather.propagation.distance_km': '累计传播距离',
  'weather.risk_factors': '气象风险因素',
  'restriction.upwind_roads.match_score': '上风向道路匹配评分',
  'restriction.sensitive_pois.by_type': '敏感目标类别'
}

const CATEGORY_DISPLAY_NAMES: Record<string, string> = {
  private_car: '私家车',
  taxi: '的士',
  bus: '巴士',
  light_goods_vehicle: '轻型货车',
  heavy_goods_vehicle: '重型货车',
  traffic: '交通源',
  traffic_source: '交通源',
  regional_transport: '区域输送',
  regional_transport_score: '区域输送',
  local_accumulation: '局地积聚',
  local_accumulation_score: '局地积聚',
  photochemical: '光化学污染',
  photochemical_score: '光化学污染',
  dust_source: '扬尘源',
  photochemical_source: '光化学污染',
  no2: '二氧化氮',
  nox: '氮氧化物',
  pm10: 'PM10',
  pm25: 'PM2.5',
  o3: '臭氧',
  so2: '二氧化硫',
  co: '一氧化碳',
  'private car': '私家车',
  'franchised bus dd': '双层专营巴士',
  'franchised bus sd': '单层专营巴士',
  'non franchised bus': '非专营巴士',
  'goods vehicle light': '轻型货车',
  'goods vehicle m&h': '中重型货车',
  'motor cycle': '摩托车',
  'private lb': '私家小巴',
  plb: '公共小巴',
  traffic_pressure_score: '交通压力筛查评分',
  avg_aqhi: '平均 AQHI',
  'local accumulation': '局地积聚',
  'regional transport': '区域输送',
  'traffic source': '交通源',
  'dust source': '扬尘源',
  'photochemical source': '光化学污染'
}

const CHART_TYPE_LABELS: Record<string, string> = {
  line: '趋势图',
  area: '面积趋势图',
  bar: '柱状图',
  stacked_bar: '堆叠柱状图',
  pie: '饼图',
  donut: '环形图',
  radar: '雷达图'
}

export function metricDisplayName(metric: string): string {
  const normalized = metric.toLowerCase()
  const segments = normalized.split('.')
  const leaf = segments[segments.length - 1] ?? ''
  return METRIC_DISPLAY_NAMES[normalized]
    ?? CATEGORY_DISPLAY_NAMES[normalized]
    ?? CATEGORY_DISPLAY_NAMES[leaf]
    ?? '环境指标'
}

export function categoryDisplayName(category: string): string {
  return METRIC_DISPLAY_NAMES[category.toLowerCase()]
    ?? CATEGORY_DISPLAY_NAMES[category.toLowerCase()]
    ?? '其他类别'
}

/** Keep real place names and timestamps while translating known data categories. */
export function chartCategoryLabel(category: string): string {
  const normalized = category.trim().toLowerCase()
  if (METRIC_DISPLAY_NAMES[normalized]) return METRIC_DISPLAY_NAMES[normalized]
  if (CATEGORY_DISPLAY_NAMES[normalized]) return CATEGORY_DISPLAY_NAMES[normalized]
  if (/^[\d:.\-\s]+$/.test(category) || /[\u3400-\u9fff]/.test(category)) return category
  const withoutSuffix = normalized.replace(/_score$/, '')
  return CATEGORY_DISPLAY_NAMES[withoutSuffix] ?? category
}

export function chartSeriesLabel(name: string, metric: string): string {
  if (/[\u3400-\u9fff]/.test(name)) return name
  const normalized = name.trim().toLowerCase()
  return METRIC_DISPLAY_NAMES[normalized]
    ?? CATEGORY_DISPLAY_NAMES[normalized]
    ?? METRIC_DISPLAY_NAMES[metric.toLowerCase()]
    ?? metricDisplayName(metric)
}

export function chartDisplayTitle(title: string, metrics: string[], type: string): string {
  const translated = Object.entries(METRIC_DISPLAY_NAMES).reduce(
    (text, [key, label]) => text.split(key).join(label), title.trim())
  if (translated && !/[a-z]{3,}/i.test(translated)) return translated
  const names = metrics.map(metricDisplayName).filter(name => name !== '环境指标')
  const base = [...new Set(names)].join('与') || '数据'
  return `${base}${CHART_TYPE_LABELS[type] ?? '图表'}`
}

export function visualizationDisplayTitle(visualization: ActiveVisualization | null): string {
  const spec = visualization?.spec
  if (!spec) return 'AI 可视化'
  let title = spec.title?.trim() ?? ''
  for (const [key, label] of Object.entries(METRIC_DISPLAY_NAMES)) {
    title = title.split(key).join(label)
  }
  if (title && !/[a-z]{3,}/i.test(title)) return title
  const metrics = spec.metrics.map(metricDisplayName).join('、')
  return `${metrics || '环境指标'}${CHART_TYPE_LABELS[spec.chart_type ?? ''] ?? '图表'}`
}

function metricValues(metric: VisualizationMetricData): VisualizationMetricValue[] {
  return Array.isArray(metric.values) ? metric.values : []
}

function numericValue(item: VisualizationMetricValue): number | null {
  if (typeof item.value === 'number' && Number.isFinite(item.value)) return item.value
  if (typeof item.avg === 'number' && Number.isFinite(item.avg)) return item.avg
  return null
}

function isTimeSeries(metric: VisualizationMetricData): boolean {
  return metricValues(metric).some((item) => typeof item.time === 'string')
}

function hourlyAxisLabels(timestamps: string[]): string[] {
  const parsed = timestamps.map((timestamp) => {
    const match = timestamp.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/)
    return match
      ? { day: `${match[2]}-${match[3]}`, time: `${match[4]}:${match[5]}` }
      : { day: '', time: timestamp }
  })
  const spansMultipleDays = new Set(parsed.map((item) => item.day).filter(Boolean)).size > 1
  return parsed.map((item) => (
    spansMultipleDays && item.day ? `${item.day} ${item.time}` : item.time
  ))
}

function timeSeriesOption(
  visualization: ActiveVisualization,
  metrics: VisualizationMetricData[]
): EChartsOption {
  const timestamps = Array.from(new Set(metrics.flatMap((metric) => metricValues(metric)
    .filter((item) => typeof item.time === 'string')
    .map((item) => item.time as string)))).sort((left, right) => left.localeCompare(right))
  const timeLabels = hourlyAxisLabels(timestamps)
  const units = Array.from(new Set(metrics.map((metric) => metric.unit).filter(Boolean))) as string[]
  const explicitIndexes = visualization.spec?.grouping?.y_axis_index_by_metric
  const axisFor = (metric: VisualizationMetricData): number => {
    const explicitIndex = explicitIndexes?.[metric.metric]
    if (typeof explicitIndex === 'number' && Number.isInteger(explicitIndex) && explicitIndex >= 0) {
      return explicitIndex
    }
    if (units.length <= 1) return 0
    const explicit = visualization.spec?.grouping?.y_axis_by_metric?.[metric.metric]
    if (explicit) return explicit === 'right' ? 1 : 0
    return units.indexOf(metric.unit || '') > 0 ? 1 : 0
  }
  const axisCount = Math.max(1, ...metrics.map((metric) => axisFor(metric) + 1))
  const multipleAxes = axisCount > 1
  const hasBarSeries = metrics.some((metric) => (
    visualization.spec?.grouping?.series_type_by_metric?.[metric.metric] === 'bar'
  ))
  const series: SeriesItem[] = metrics.map((metric) => {
    const byTime = new Map(metricValues(metric).map((item) => [item.time, numericValue(item)]))
    const seriesType = visualization.spec?.grouping?.series_type_by_metric?.[metric.metric] ?? 'line'
    const isArea = visualization.spec?.chart_type === 'area' && seriesType === 'line'
    const circular = metric.semantic_type === 'circular_direction'
    return {
      name: metricDisplayName(metric.metric),
      type: seriesType,
      data: timestamps.map((time) => byTime.get(time) ?? null),
      yAxisIndex: axisFor(metric),
      areaStyle: isArea ? {} : undefined,
      barMaxWidth: seriesType === 'bar' ? 28 : undefined,
      itemStyle: seriesType === 'bar'
        ? { opacity: 0.72, borderRadius: [3, 3, 0, 0] }
        : undefined,
      connectNulls: false,
      smooth: false,
      emphasis: { focus: 'series' },
      ...(circular ? { smooth: false } : {})
    }
  })
  const yAxis = Array.from({ length: axisCount }, (_, index) => ({
    type: 'value' as const,
    name: (() => {
      const axisMetrics = metrics.filter((metric) => axisFor(metric) === index)
      const names = axisMetrics.map((metric) => metricDisplayName(metric.metric)).join(' / ')
      const unit = axisMetrics[0]?.unit || units[index] || ''
      return unit ? displayUnit(unit) : names
    })(),
    position: index % 2 === 0 ? 'left' as const : 'right' as const,
    offset: index < 2 ? 0 : Math.floor(index / 2) * 52,
    axisLabel: { formatter: '{value}' },
    splitLine: { show: index === 0 }
  }))
  return {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', bottom: 0 },
    grid: {
      left: axisCount > 2 ? 112 : 56,
      right: multipleAxes ? (axisCount > 3 ? 112 : 70) : 28,
      top: 40,
      bottom: 76,
      containLabel: true
    },
    // A line-only chart places points on the two plot boundaries. Mixed
    // line/bar charts need category padding, otherwise the first/last rain bar
    // is centred on the boundary and half of it is drawn outside the grid.
    xAxis: {
      type: 'category',
      boundaryGap: hasBarSeries,
      data: timeLabels,
      name: '时间（香港时间）',
      nameLocation: 'middle',
      nameGap: 34,
      axisLabel: {
        interval: 0,
        rotate: timeLabels.length > 8 ? 30 : 0,
        hideOverlap: false
      }
    },
    yAxis,
    series: series as unknown as NonNullable<EChartsOption['series']>
  }
}

function categoryOption(
  visualization: ActiveVisualization,
  metrics: VisualizationMetricData[]
): EChartsOption {
  const values = metrics[0] ? metricValues(metrics[0]) : []
  const categories = values.map((item) => categoryDisplayName(item.category || ''))
  const chartType = visualization.spec?.chart_type || 'bar'
  if (metrics[0]?.semantic_type === 'window_aggregate_concentration_profile') {
    const microValues = values.map((item) => item.unit === 'μg/m³' ? numericValue(item) : null)
    const coValues = values.map((item) => item.unit === 'mg/m³' ? numericValue(item) : null)
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['时段平均浓度（μg/m³）', '一氧化碳（mg/m³）'] },
      grid: { left: 72, right: 72, top: 64, bottom: 64, containLabel: true },
      xAxis: {
        type: 'category',
        data: categories,
        axisLabel: { interval: 0 }
      },
      yAxis: [
        { type: 'value', name: '浓度（μg/m³）', position: 'left' },
        { type: 'value', name: 'CO（mg/m³）', position: 'right' }
      ],
      series: [
        {
          name: '时段平均浓度（μg/m³）', type: 'bar', data: microValues,
          yAxisIndex: 0, barMaxWidth: 42
        },
        {
          name: '一氧化碳（mg/m³）', type: 'bar', data: coValues,
          yAxisIndex: 1, barMaxWidth: 42
        }
      ]
    }
  }
  if (chartType === 'donut' || chartType === 'pie') {
    const isVehicleComposition = metrics[0]?.semantic_type === 'vehicle_composition'
    return {
      tooltip: {
        trigger: 'item',
        formatter: isVehicleComposition ? '{b}：{c}%' : '{b}：{c}'
      },
      legend: { type: 'scroll', bottom: 0 },
      series: [{
        type: 'pie',
        radius: chartType === 'donut' ? ['42%', '72%'] : '65%',
        data: values
          .map((item) => ({ name: categoryDisplayName(item.category || ''), value: numericValue(item) }))
          .filter((item): item is { name: string; value: number } => item.value !== null),
        emphasis: { focus: 'self' }
      }]
    }
  }
  if (chartType === 'radar') {
    const numeric = values.map(numericValue).map((value) => value ?? 0)
    const maxValue = Math.max(...numeric, 1)
    return {
      tooltip: { trigger: 'item' },
      radar: { indicator: categories.map((name) => ({ name, max: maxValue * 1.1 })) },
      series: [{ type: 'radar', data: [{ value: numeric, name: metrics[0] ? metricDisplayName(metrics[0].metric) : '指标' }] }]
    }
  }
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 56, right: 28, top: 56, bottom: 64, containLabel: true },
    xAxis: { type: 'category', data: categories, axisLabel: { interval: 0, rotate: categories.length > 6 ? 30 : 0 } },
    yAxis: {
      type: 'value',
      name: metrics[0]
        ? `${metricDisplayName(metrics[0].metric)}${metrics[0].unit ? `（${displayUnit(metrics[0].unit)}）` : ''}`
        : ''
    },
    series: metrics.map((metric) => ({
      name: metricDisplayName(metric.metric),
      type: chartType === 'stacked_bar' ? 'bar' : 'bar',
      stack: chartType === 'stacked_bar' ? 'total' : undefined,
      data: metricValues(metric).map(numericValue),
      emphasis: { focus: 'series' }
    }))
  }
}

function displayUnit(unit: string): string {
  if (unit === 'AQHI index') return 'AQHI 指数'
  if (unit === 'EU') return '吸入当量'
  if (unit === 'dimensionless independent screening score') return '独立筛查评分'
  if (unit === 'dimensionless screening score') return '筛查评分'
  return unit
}

/** Build a safe ECharts option from the validated semantic response only. */
export function buildAIVisualizationOption(
  visualization: ActiveVisualization | null
): EChartsOption | null {
  if (!visualization?.valid || visualization.status === 'reset' || !visualization.spec || !visualization.data) {
    return null
  }
  const metrics = visualization.spec.metrics
    .map((key) => visualization.data?.metrics[key])
    .filter((metric): metric is VisualizationMetricData => Boolean(metric && Array.isArray(metric.values)))
  if (!metrics.length || metrics.every((metric) => metric.values.length === 0)) return null
  const option = metrics.some(isTimeSeries)
    ? timeSeriesOption(visualization, metrics)
    : categoryOption(visualization, metrics)
  return {
    ...option,
    color: ['#21318c', '#30a5c2', '#9bd5b9', '#eef5b2', '#082059',
      '#2254a3', '#1e80b8', '#62bfbf', '#d5ebbd', '#fdfed7'],
    animation: false,
    title: { text: visualizationDisplayTitle(visualization), left: 'left' }
  }
}
