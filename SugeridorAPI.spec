# -*- mode: python ; coding: utf-8 -*-
# Bundle onefile de run_api.py. Huella real de dependencias confirmada por
# auditoría de imports (ver plan de migración): pandas, numpy, openpyxl,
# fastapi, uvicorn, httpx, pydantic, python-dotenv, boto3/botocore,
# python-multipart. Nada de streamlit/scikit-learn/sentence-transformers/
# polars/dask — no los toca api.py, se excluyen explícito por si PyInstaller
# los detecta igual vía algún import transitivo perdido.
from PyInstaller.utils.hooks import collect_submodules

hidden_imports = (
    # openpyxl nunca se importa por nombre (solo como string en
    # pd.ExcelWriter(engine="openpyxl") / implícito en pd.read_excel) —
    # el análisis estático de PyInstaller no lo detecta solo.
    ["openpyxl"]
    # run_api.py importa `api` dentro de `if __name__ == "__main__":` — el
    # análisis estático de PyInstaller no siempre sigue imports anidados en
    # el guard, así que se listan explícitos junto con todo lo que api.py
    # importa transitivamente (módulos locales del proyecto, no paquetes
    # instalados — por eso no sirve collect_submodules aquí).
    + ["api", "config", "io_loaders", "r2_client"]
    + collect_submodules("procesadores")
    + collect_submodules("reportes")
    + collect_submodules("sugerencias")
    # uvicorn resuelve su loop/protocolo por import dinámico según el SO.
    + collect_submodules("uvicorn")
    + collect_submodules("fastapi")
)

a = Analysis(
    ["run_api.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    excludes=[
        "streamlit", "sklearn", "scikit-learn", "sentence_transformers",
        "polars", "dask", "Levenshtein", "plotly", "pyarrow", "tqdm",
        "unidecode", "xlsxwriter", "matplotlib", "tkinter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SugeridorAPI",
    console=True,   # ventana visible: es el "indicador" de que sigue corriendo
)
