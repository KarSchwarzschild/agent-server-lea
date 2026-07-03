/**
 * ECharts 图表配置生成器
 * 来源：PPTist (MIT License)
 */
import type { ChartData, ChartType } from './types'

export interface ChartOptionPayload {
  type: ChartType
  data: ChartData
  themeColors: string[]
  textColor?: string
  lineColor?: string
  lineSmooth?: boolean
  stack?: boolean
}

export const getChartOption = ({
  type, data, themeColors, textColor, lineColor, lineSmooth, stack,
}: ChartOptionPayload): Record<string, any> | null => {
  const textStyle = textColor ? { color: textColor } : {}
  const axisLine = textColor ? { lineStyle: { color: textColor } } : undefined
  const axisLabel = textColor ? { color: textColor } : undefined
  const splitLine = lineColor ? { lineStyle: { color: lineColor } } : {}
  const legend = data.series.length > 1 ? { top: 'bottom', textStyle } : undefined

  if (type === 'bar') {
    return {
      color: themeColors, textStyle, legend,
      xAxis: { type: 'category', data: data.labels, axisLine, axisLabel },
      yAxis: { type: 'value', axisLine, axisLabel, splitLine },
      series: data.series.map((item, index) => {
        const s: any = { data: item, name: data.legends[index], type: 'bar', label: { show: true }, itemStyle: { borderRadius: [2, 2, 0, 0] } }
        if (stack) s.stack = 'A'
        return s
      }),
    }
  }
  if (type === 'column') {
    return {
      color: themeColors, textStyle, legend,
      yAxis: { type: 'category', data: data.labels, axisLine, axisLabel },
      xAxis: { type: 'value', axisLine, axisLabel, splitLine },
      series: data.series.map((item, index) => {
        const s: any = { data: item, name: data.legends[index], type: 'bar', label: { show: true }, itemStyle: { borderRadius: [0, 2, 2, 0] } }
        if (stack) s.stack = 'A'
        return s
      }),
    }
  }
  if (type === 'line') {
    return {
      color: themeColors, textStyle, legend,
      xAxis: { type: 'category', data: data.labels, axisLine, axisLabel },
      yAxis: { type: 'value', axisLine, axisLabel, splitLine },
      series: data.series.map((item, index) => {
        const s: any = { data: item, name: data.legends[index], type: 'line', smooth: lineSmooth, label: { show: true } }
        if (stack) s.stack = 'A'
        return s
      }),
    }
  }
  if (type === 'pie' || type === 'ring') {
    return {
      color: themeColors, textStyle,
      legend: { top: 'bottom', textStyle },
      series: [{
        data: data.series[0].map((item, index) => ({ value: item, name: data.labels[index] })),
        label: textColor ? { color: textColor } : {},
        type: 'pie',
        radius: type === 'ring' ? ['40%', '70%'] : '70%',
        ...(type === 'ring' ? { padAngle: 1, itemStyle: { borderRadius: 4 } } : {}),
        emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)' }, label: { show: true, fontSize: 14, fontWeight: 'bold' } },
      }],
    }
  }
  if (type === 'area') {
    return {
      color: themeColors, textStyle, legend,
      xAxis: { type: 'category', boundaryGap: false, data: data.labels, axisLine, axisLabel },
      yAxis: { type: 'value', axisLine, axisLabel, splitLine },
      series: data.series.map((item, index) => {
        const s: any = { data: item, name: data.legends[index], type: 'line', areaStyle: {}, label: { show: true } }
        if (stack) s.stack = 'A'
        return s
      }),
    }
  }
  if (type === 'radar') {
    return {
      color: themeColors, textStyle, legend,
      radar: {
        indicator: data.labels.map(item => ({ name: item })),
        splitLine,
        axisLine: lineColor ? { lineStyle: { color: lineColor } } : undefined,
      },
      series: [{ data: data.series.map((item, index) => ({ value: item, name: data.legends[index] })), type: 'radar' }],
    }
  }
  if (type === 'scatter') {
    const formatedData = data.series[0].map((x, i) => [x, data.series[1]?.[i] ?? x])
    return {
      color: themeColors, textStyle,
      xAxis: { axisLine, axisLabel, splitLine },
      yAxis: { axisLine, axisLabel, splitLine },
      series: [{ symbolSize: 12, data: formatedData, type: 'scatter' }],
    }
  }
  return null
}
