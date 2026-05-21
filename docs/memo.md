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
