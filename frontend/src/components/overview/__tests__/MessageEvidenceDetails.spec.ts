// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import MessageEvidenceDetails from '../MessageEvidenceDetails.vue'
import type { AIChatResponse } from '@/types/aiChat'

const response: AIChatResponse = {
  contract_version: '1.0',
  answer: '兼容文本不应作为推荐主体展示',
  business_action: 'fallback',
  visualization_action: 'none',
  route: null,
  context: { station_name: 'Mong Kok Roadside', start_time: '2023-08-15 08:00:00', end_time: '2023-08-15 11:00:00' },
  agent_results: {},
  user_goal: 'public_travel_advice',
  recommendation_result: {
    recommendation_type: 'public_travel_advice',
    summary: '历史时段建议',
    recommendation: 'choose_conditionally_and_avoid_busy_road_exposure',
    evidence: [{ type: 'wind_speed', value: 1.19, unit: 'm/s' }],
    evidence_summary: { status: 'partial', available: ['meteorology'], missing_required: [], missing_optional: [], reason: 'test' },
    limitations: ['route_specific_exposure_unavailable'],
    confidence: 'medium'
  },
  recommendation_display: {
    version: '1.0',
    recommendation_type: 'public_travel_advice',
    title: '居民出行建议',
    direct_advice: [{ title: '减少停留', detail: '减少近道路停留。' }],
    explanation: '历史解释',
    evidence: [{
      category: 'observed', label: '平均风速', value: '1.19', unit: 'm/s',
      source: 'MeteorologyAgent', time_basis: '2023-08-15 08:00:00 至 11:00:00'
    }],
    scope: { station_name: 'Mong Kok Roadside' },
    markdown_fallback: ''
  }
}

describe('MessageEvidenceDetails', () => {
  it('is collapsed by default and toggles without requesting new data', async () => {
    const wrapper = mount(MessageEvidenceDetails, {
      props: { response },
      global: { plugins: [ElementPlus] }
    })

    expect(wrapper.text()).toContain('查看建议依据')
    expect(wrapper.find('.el-collapse-item').classes()).not.toContain('is-active')
    await wrapper.get('.el-collapse-item__header').trigger('click')
    await nextTick()
    expect(wrapper.find('.el-collapse-item').classes()).toContain('is-active')
    expect(wrapper.text()).toContain('平均风速')
    await wrapper.get('.el-collapse-item__header').trigger('click')
    await nextTick()
    expect(wrapper.find('.el-collapse-item').classes()).not.toContain('is-active')
  })

  it('keeps professional knowledge inside the default collapsed advice evidence', async () => {
    const withKnowledge: AIChatResponse = {
      ...response,
      recommendation_display: {
        ...response.recommendation_display!,
        professional_knowledge: {
          summary: '低风速会影响**污染物扩散**，建议结合本次天气安排出行。',
          sources: [{ document_id: 'verified-source', title: '环境出行参考资料', author: '测试作者' }]
        }
      }
    }
    const wrapper = mount(MessageEvidenceDetails, {
      props: { response: withKnowledge }, global: { plugins: [ElementPlus] }
    })
    expect(wrapper.get('.collapse-title').text()).toContain('查看建议依据')
    expect(wrapper.get('.el-collapse-item__header').attributes('aria-expanded')).toBe('false')
    const supplement = wrapper.get('.el-collapse-item__content .knowledge-supplement')
    expect(supplement.isVisible()).toBe(false)
    await wrapper.get('.el-collapse-item__header').trigger('click')
    await nextTick()
    expect(wrapper.get('.el-collapse-item__header').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.el-collapse-item__wrap').attributes('aria-hidden')).toBe('false')
    expect(supplement.text()).toContain('专业知识补充')
    expect(supplement.get('strong').text()).toBe('污染物扩散')
    expect(supplement.text()).toContain('环境出行参考资料')
    expect(wrapper.findAll('.knowledge-supplement')).toHaveLength(1)
  })

  it('offers advice evidence when professional knowledge is the only evidence', () => {
    const wrapper = mount(MessageEvidenceDetails, {
      props: { response: {
        ...response, recommendation_result: undefined,
        recommendation_display: {
          ...response.recommendation_display!, evidence: [],
          professional_knowledge: { summary: '专业知识补充内容。', sources: [] }
        }
      } },
      global: { plugins: [ElementPlus] }
    })
    expect(wrapper.get('.collapse-title').text()).toContain('查看建议依据')
    expect(wrapper.get('.el-collapse-item__header').attributes('aria-expanded')).toBe('false')
  })

})
