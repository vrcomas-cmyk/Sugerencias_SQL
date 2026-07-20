"""
run_api.py - Entrypoint para el .exe portable (PyInstaller).

`uvicorn api:app --reload` no sirve empaquetado (--reload reimporta módulos
por ruta de archivo, que no existen dentro del .exe) — este script llama a
uvicorn programáticamente en vez de por CLI, sin reload, un solo proceso.
"""
import sys

if __name__ == "__main__":
    try:
        import uvicorn
        from api import app  # puede terminar el proceso (sys.exit) si falta el .env — se atrapa abajo

        print("Sugeridor de Materiales — API")
        print("Corriendo en http://localhost:8000 — deja esta ventana abierta mientras la uses.")
        print("Ciérrala (o Ctrl+C) cuando termines.\n")
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        if e.code not in (0, None):
            input("\nPresiona Enter para cerrar...")
        raise
    except Exception as e:  # noqa: BLE001
        print(f"\nERROR: {e}")
        input("Presiona Enter para cerrar...")
        sys.exit(1)
