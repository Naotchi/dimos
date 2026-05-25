# DGX-Spark Install
- aarch64はmanipulationなど一部のextrasに対応していないので、個別で指定してインストールする
```
 uv sync \
    --extra agents --extra apriltag --extra base --extra cpu --extra cuda \
    --extra docker --extra drone --extra misc --extra perception \
    --extra psql --extra sim --extra unitree --extra visualization --extra web
```

dimos --simulation run unitree-go2-basic


 torch 2.9.1+cu130 cp312 aarch64 wheel

## DGX Spark
- LM Studioでqwen3.6-35b-a3bをロード、context lengthを32768、enable thinkingをoff

## 実機
STA / AP modeをアプリで切り替える。切り替え後はアプリを落とす。
### STA
### AP