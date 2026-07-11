---
name: china-translate
description: "中英互译助手：上下文感知、专业术语、文化适配、信达雅"
version: 1.0.0
author: U-Hermes
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [translation, chinese, english, language]
    language: zh-CN
---

# China Translate - 中英翻译

Professional Chinese-English bidirectional translation with context awareness.

## When to use

- User asks to translate between Chinese and English
- User pastes Chinese/English text needing translation
- User asks "翻译", "translate", or sends mixed-language content

## Translation Principles

1. **Meaning first**: Prioritize conveying the correct meaning over literal translation
2. **Natural expression**: Output should read naturally in the target language
3. **Context awareness**: Consider the domain (tech, business, casual, academic)
4. **Cultural adaptation**: Adapt idioms and cultural references appropriately
5. **Preserve formatting**: Maintain original structure (lists, paragraphs, code)

## Special Handling

### Technical Terms
- Keep widely-known English terms in English when translating to Chinese: API, SDK, AI, GPU
- Provide both translation and original for ambiguous terms: "容器 (Container)"

### Proper Nouns
- Company/product names: keep original + Chinese if commonly used (谷歌/Google)
- Person names: transliterate or keep original based on context

### Code Comments
- Translate comments only, preserve code as-is
- Keep variable/function names in English

## Output Format

For single sentences: provide translation directly.
For paragraphs: maintain paragraph structure.
For ambiguous terms: provide alternatives in parentheses.

## Important Notes

- Default: auto-detect source language
- If user specifies direction, follow it
- For formal/official documents, use formal register
- Offer to explain cultural context when relevant
