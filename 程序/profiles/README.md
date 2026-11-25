# 物品尺寸目录

34 件物品的工作尺寸。生成程序读取 `object_profiles.json`。六视图文件名与物品名称一致。

| 文件 | 用途 |
|---|---|
| `object_profile_seeds.json` | 尺寸来源。改这里。 |
| `object_profiles.json` | 运行时目录，由种子生成。 |
| `物品尺寸手册.md` | 逐件尺寸，不含旧提示词。 |

在「程序」目录里修改种子后重建：

```bash
python3 tools/build_object_profiles.py
```

生成一张图：

```bash
python3 generate_openai.py --model gpt-image-2 --quality low --budget-usd 5 \
  --objects S15 --backgrounds 2026_08_06_36_1_zhongfei1_95_zhongfei2_5_19.jpg
```

密钥放在「程序」目录的 `config_openai.yaml`，或导出 `OPENAI_API_KEY`。不要把密钥放进这个目录。
