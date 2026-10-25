import { expect, it } from 'vitest'
import type { ActiveVisualization } from '@/types/aiChat'
import { buildAIVisualizationOption, visualizationAnswerText } from '../aiVisualization'

it('sorts combined time series and keeps values aligned with the correct hour', () => {
  const no2 = 'air_quality.hourly.no2'
  const aqhi = 'air_quality.hourly.aqhi'
  const visualization = {
    status: 'planned', valid: true, revision: 1,
    spec: { metrics: [no2, aqhi], chart_type: 'line', grouping: {
      y_axis_by_metric: { [no2]: 'left', [aqhi]: 'right' }
    } },
    data: { metrics: {
      [no2]: { metric: no2, unit: 'μg/m³', values: [
        { time: '2023-08-15 09:00:00', value: 53 },
        { time: '2023-08-15 10:00:00', value: 62 }
      ] },
      [aqhi]: { metric: aqhi, unit: 'AQHI index', values: [
        { time: '2023-08-15 08:00:00', value: 2 },
        { time: '2023-08-15 09:00:00', value: 3 }
      ] }
    } }
  } as unknown as ActiveVisualization
  const option = buildAIVisualizationOption(visualization) as any
  expect(option.xAxis.data).toEqual(['08:00', '09:00', '10:00'])
  expect(option.series[0].data).toEqual([null, 53, 62])
  expect(option.series[1].data).toEqual([2, 3, null])
  expect(option.legend.bottom).toBe(0)
  expect(option.yAxis[0].axisLabel.formatter).toBe('{value}')
  expect(option.yAxis[1].name).toBe('AQHI 指数')
  expect(option.yAxis[1].splitLine.show).toBe(false)
})

it('removes retired implementation notes from restored chart answers', () => {
  expect(visualizationAnswerText('已生成趋势图。数据来自当前可信业务分析结果。 注意：不同物理量已通过 grouping.y_axis_by_metric 显式分轴。'))
    .toBe('已生成趋势图。')
  expect(visualizationAnswerText('不同物理量已通过 grouping.y_axis_index_by_metric 显式分轴。')).toBe('')
})
