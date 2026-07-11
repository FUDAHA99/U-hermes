---
name: china-weather
description: "中国城市天气查询：实时天气、未来预报、穿衣建议（免费API）"
version: 1.0.0
author: U-Hermes
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  commands: [curl]
metadata:
  hermes:
    tags: [weather, chinese, api, utility]
    language: zh-CN
---

# China Weather - 天气查询

Get current weather and forecasts for Chinese cities using free public APIs.

## When to use

- User asks about weather in a Chinese city
- User needs weather data for planning (travel, events)
- User asks "天气怎么样" or similar

## API Endpoint

Use wttr.in (no API key needed, supports Chinese city names):

```bash
curl -s "wttr.in/北京?format=j1&lang=zh"
```

For formatted output:
```bash
curl -s "wttr.in/上海?lang=zh&format=%l:+%c+%t+%h+%w"
```

## City Name Mapping

Use Chinese city names directly (pinyin also works):
- 北京 / Beijing
- 上海 / Shanghai
- 广州 / Guangzhou
- 深圳 / Shenzhen

## Response Format

Present weather information clearly:
- Current temperature and conditions
- Humidity and wind
- 3-day forecast if available
- Clothing/umbrella suggestions based on conditions

## Important Notes

- wttr.in is free and requires no API key
- Fallback: if wttr.in is unavailable, suggest user check weather apps
- Always mention the query time (weather data freshness)
- Include practical advice (e.g., "建议带伞" if rain expected)
