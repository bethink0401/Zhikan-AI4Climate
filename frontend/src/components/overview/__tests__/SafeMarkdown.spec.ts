// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SafeMarkdown from '../SafeMarkdown.vue'

describe('SafeMarkdown', () => {
  it('renders controlled Markdown and removes SVG artefacts without executing HTML', () => {
    const wrapper = mount(SafeMarkdown, {
      props: { content: '**重点建议**\n- 第一项\n_svg_\n<svg onload="alert(1)"><text>bad</text></svg>' }
    })

    expect(wrapper.get('strong').text()).toBe('重点建议')
    expect(wrapper.get('li').text()).toBe('第一项')
    expect(wrapper.html()).not.toContain('_svg_')
    expect(wrapper.html()).not.toContain('<svg')
    expect(wrapper.html()).not.toContain('onload')
  })
})
