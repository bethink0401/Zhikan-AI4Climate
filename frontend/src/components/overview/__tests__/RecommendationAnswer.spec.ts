// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import RecommendationAnswer from '../RecommendationAnswer.vue'
import type { RecommendationDisplay } from '@/types/aiChat'

const display: RecommendationDisplay = {
  version: '1.0',
  recommendation_type: 'public_travel_advice',
  title: '居民出行建议',
  direct_advice: [
    { title: '减少近道路停留', detail: '不要在繁忙道路旁长时间等候。' },
    { title: '出发前查询信息', detail: '查询最新 AQHI 和交通信息。' },
    { title: '灵活安排出行', detail: '根据实时情况调整时段。' }
  ],
  explanation: '这些建议依据所选历史时段的业务分析。',
  evidence: [],
  scope: { station_name: 'Mong Kok Roadside' },
  markdown_fallback: ''
}

describe('RecommendationAnswer', () => {
  it('renders model-selected focus in advice and explanations as bold text', () => {
    const wrapper = mount(RecommendationAnswer, { props: { display: {
      ...display,
      direct_advice: [{ title: '关注重点路段', detail: '优先核查**上风向道路**，并减少敏感点暴露。' }],
      explanation: '**低风速不利扩散**，因此优先减少近道路暴露。'
    } } })
    expect(wrapper.get('.advice-detail strong').text()).toBe('上风向道路')
    expect(wrapper.get('.reason-section strong').text()).toBe('低风速不利扩散')
    expect(wrapper.text()).not.toContain('**')
  })

  it('renders direct advice before the explanation without an old risk card', () => {
    const wrapper = mount(RecommendationAnswer, { props: { display } })
    const text = wrapper.text()

    expect(wrapper.get('h3').text()).toBe('居民出行建议')
    expect(wrapper.findAll('.advice-item')).toHaveLength(3)
    expect(text.indexOf('减少近道路停留')).toBeLessThan(text.indexOf('为什么这样建议'))
    expect(text).not.toContain('证据完整度')
    expect(text).not.toContain('85/100')
    expect(text).not.toContain('未来1—3小时')
  })
})
