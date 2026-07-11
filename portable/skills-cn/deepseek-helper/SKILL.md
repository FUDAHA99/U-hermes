---
name: deepseek-helper
description: "DeepSeek 模型调优：提示词优化、代码生成、推理链设计、成本控制"
version: 1.0.0
author: U-Hermes
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [deepseek, coding, optimization, chinese, ai-tools]
    language: zh-CN
---

# DeepSeek Helper - DeepSeek 模型优化助手

Helps users get the most out of DeepSeek models with optimized prompts, code generation patterns, and cost-saving strategies.

## When to use

- User is using DeepSeek as their model and asks for coding help
- User wants to optimize their DeepSeek usage or costs
- User asks about DeepSeek-specific features (FIM, reasoning, etc.)

## DeepSeek Model Knowledge

### Available Models
| Model | Best For | Context | Price (per 1M tokens) |
|-------|----------|---------|----------------------|
| deepseek-chat | General + Code | 64K | Input ¥1, Output ¥2 |
| deepseek-reasoner | Complex reasoning | 64K | Input ¥4, Output ¥16 |

### Key Capabilities
- **Fill-in-the-Middle (FIM)**: Code completion with prefix + suffix context
- **JSON Mode**: Structured output with `response_format: {"type": "json_object"}`
- **Function Calling**: Full tool-use support
- **Multi-turn reasoning**: Chain-of-thought with deepseek-reasoner

## Prompt Optimization Tips

1. **Be specific with language**: DeepSeek excels when you specify the programming language and framework explicitly.

2. **Use Chinese for Chinese tasks**: DeepSeek handles Chinese exceptionally well. Write prompts in Chinese when the output should be Chinese.

3. **Code structure**: For large code generation, provide:
   - File structure overview
   - Key interfaces/types first
   - Implementation after

4. **Cost optimization**:
   - Use `deepseek-chat` for most tasks (10x cheaper than reasoner)
   - Only use `deepseek-reasoner` for complex math/logic problems
   - Keep system prompts concise (they count toward input tokens)
   - Use caching-friendly prompt ordering (static content first)

## Integration Notes

- API endpoint: `https://api.deepseek.com/v1`
- Fully OpenAI-compatible API format
- Supports streaming, function calling, JSON mode
- Rate limits: generous for paid tier
- No content filtering on code generation
