"""
NeuroForge BioMaterials — простой прототип пайплайна
Для студенческого грантового демо.
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
from PIL import Image
import io
import os
import uuid
import tempfile
from pathlib import Path

import asyncio
import multiprocessing as mp
import shutil
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

try:
    from backend import cranio
except ImportError:  # запуск как python backend/app.py
    import cranio

# Optional heavy imports
try:
    from skimage import filters, measure, morphology
    from skimage.color import rgb2gray
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

try:
    import trimesh
    HAS_TRIMESH = True
except ImportError:
    HAS_TRIMESH = False

app = FastAPI(title="NeuroForge BioMaterials Demo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Папка для временных файлов
OUTPUT_DIR = Path(tempfile.gettempdir()) / "neuroforge_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def generate_gyroid_mesh(size=20, resolution=24, thickness=0.35):
    """
    Генерирует приближённый gyroid (TPMS) каркас.
    Возвращает trimesh.Trimesh или None.
    """
    if not HAS_TRIMESH:
        return None

    # Сетка
    x = np.linspace(-np.pi, np.pi, resolution)
    y = np.linspace(-np.pi, np.pi, resolution)
    z = np.linspace(-np.pi, np.pi, resolution)
    X, Y, Z = np.meshgrid(x, y, z, indexing="ij")

    # Классический gyroid
    gyroid = np.sin(X) * np.cos(Y) + np.sin(Y) * np.cos(Z) + np.sin(Z) * np.cos(X)

    # Поверхность уровня
    level = thickness
    try:
        from skimage.measure import marching_cubes
        verts, faces, _, _ = marching_cubes(gyroid, level=level)
    except Exception:
        # fallback: простой куб с отверстиями (очень грубо)
        return create_simple_porous_box(size)

    # Нормализуем в размер size мм
    verts = verts - verts.mean(axis=0)
    scale = size / (verts.max() - verts.min())
    verts = verts * scale

    mesh = trimesh.Trimesh(vertices=verts, faces=faces)
    mesh.fix_normals()
    return mesh


def create_simple_porous_box(size=20, n_holes=4):
    """Запасной простой пористый блок, если marching cubes недоступен."""
    if not HAS_TRIMESH:
        return None
    box = trimesh.creation.box(extents=[size, size, size * 0.6])
    # Вычитаем несколько цилиндров как "поры"
    for i in range(n_holes):
        for j in range(n_holes):
            cyl = trimesh.creation.cylinder(
                radius=size * 0.08,
                height=size * 1.2,
                sections=12
            )
            offset = size * 0.7 / (n_holes - 1) if n_holes > 1 else 0
            cyl.apply_translation([
                -size/2 + size*0.15 + i * offset,
                -size/2 + size*0.15 + j * offset,
                0
            ])
            try:
                box = box.difference(cyl, engine="scad")
            except Exception:
                pass
    return box


ACTIVITY_FACTOR = {"low": 0.9, "moderate": 1.0, "high": 1.15, "athlete": 1.3}
OCCUPATION_FACTOR = {"sedentary": 0.95, "mixed": 1.0, "physical": 1.15}
ACTIVITY_RU = {"low": "низкая", "moderate": "умеренная", "high": "высокая", "athlete": "спорт на высоком уровне"}
OCCUPATION_RU = {"sedentary": "сидячая работа", "mixed": "смешанная нагрузка", "physical": "тяжёлый физический труд"}


def patient_factors(p: dict):
    """
    Учитывает параметры пациента. Все коэффициенты — грубые эвристики для демо,
    не клинические данные.
    """
    height_m = p["height_cm"] / 100.0
    bmi = p["weight_kg"] / (height_m ** 2)

    # Индекс механической нагрузки: масса тела (смягчена корнем) × активность × характер работы
    load = (p["weight_kg"] / 70.0) ** 0.5
    load *= ACTIVITY_FACTOR[p["activity"]] * OCCUPATION_FACTOR[p["occupation"]]

    # Индекс качества кости (1.0 — молодая здоровая кость): падает с возрастом, у женщин после 50 быстрее
    age = p["age"]
    if p["sex"] == "female":
        decline = 0.004 * max(age - 30, 0) + 0.008 * max(age - 50, 0)
    else:
        decline = 0.003 * max(age - 30, 0) + 0.002 * max(age - 65, 0)
    bone = 1.0 - decline
    if p["smoker"]:
        bone -= 0.05
    if p["activity"] == "low":
        bone -= 0.03
    elif p["activity"] in ("high", "athlete"):
        bone += 0.02
    bone = float(np.clip(bone, 0.5, 1.0))

    return {
        "bmi": round(bmi, 1),
        "load_index": round(float(load), 2),
        "bone_quality_index": round(bone, 2),
    }


def analyze_image(image_bytes: bytes, patient: dict):
    """
    Очень упрощённый анализ снимка + учёт параметров пациента.
    Возвращает словарь с параметрами для генерации.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_np = np.array(img)

    # Уменьшаем для скорости
    max_side = 256
    h, w = img_np.shape[:2]
    scale = min(max_side / max(h, w), 1.0)
    if scale < 1.0:
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        img_np = np.array(img)

    gray = np.mean(img_np, axis=2).astype(np.float32) / 255.0

    # Простая оценка "дефекта": тёмные области
    threshold = np.percentile(gray, 35)
    mask = gray < threshold

    area_ratio = float(mask.mean())
    approx_size_mm = max(12.0, min(40.0, (area_ratio ** 0.5) * 50))

    factors = patient_factors(patient)
    notes = []

    # Размер: масштабируем по росту (условно, ±10%)
    size_k = float(np.clip(patient["height_cm"] / 170.0, 0.9, 1.1))
    approx_size_mm = round(approx_size_mm * size_k, 1)
    if abs(size_k - 1) > 0.02:
        notes.append(f"Размер каркаса скорректирован по росту ({patient['height_cm']:.0f} см): ×{size_k:.2f}")

    # Пористость: базовая от размера дефекта, больше нагрузка — меньше пористость (прочнее)
    base_porosity = 0.55 + 0.25 * min(area_ratio * 2, 1.0)
    target_porosity = base_porosity - 0.15 * (factors["load_index"] - 1.0)
    target_porosity = float(np.clip(target_porosity, 0.45, 0.85))
    if factors["load_index"] > 1.05:
        notes.append(f"Повышенная нагрузка (индекс {factors['load_index']}): пористость снижена для прочности")
    elif factors["load_index"] < 0.95:
        notes.append(f"Пониженная нагрузка (индекс {factors['load_index']}): допустима более высокая пористость")

    # Жёсткость: эвристика для PCL, подгоняется под качество кости (мягкая кость → более податливый каркас)
    young_mpa = 800 - (target_porosity - 0.5) * 900
    young_mpa *= 0.75 + 0.25 * factors["bone_quality_index"]
    young_mpa = float(np.clip(young_mpa, 300, 950))
    if factors["bone_quality_index"] < 0.85:
        notes.append(f"Оценённое качество кости снижено (индекс {factors['bone_quality_index']}; возраст/пол/образ жизни): жёсткость подбирается мягче, чтобы не перегружать окружающую кость")

    # Материал
    if factors["load_index"] > 1.15 or factors["bone_quality_index"] < 0.7:
        material = "PCL + β-ТКФ (композит)"
        notes.append("Рекомендован композит с β-трикальцийфосфатом: лучше интеграция с костью и выше жёсткость")
    else:
        material = "PCL (поликапролактон)"

    return {
        "width": int(img_np.shape[1]),
        "height": int(img_np.shape[0]),
        "area_ratio": round(area_ratio, 3),
        "approx_size_mm": approx_size_mm,
        "target_porosity": round(target_porosity, 2),
        "young_modulus_mpa": round(young_mpa, 0),
        "fit_error_mm": round(0.4 + area_ratio * 1.2, 2),
        "material": material,
        "factors": factors,
        "notes": notes,
    }


def validate_patient(age, sex, height_cm, weight_kg, activity, occupation, smoker):
    if not 18 <= age <= 95:
        raise HTTPException(400, "Возраст должен быть от 18 до 95 лет (для детей модель не применима)")
    if sex not in ("male", "female"):
        raise HTTPException(400, "Укажите пол")
    if not 120 <= height_cm <= 230:
        raise HTTPException(400, "Рост должен быть от 120 до 230 см")
    if not 30 <= weight_kg <= 250:
        raise HTTPException(400, "Вес должен быть от 30 до 250 кг")
    if activity not in ACTIVITY_FACTOR:
        raise HTTPException(400, "Укажите уровень физической активности")
    if occupation not in OCCUPATION_FACTOR:
        raise HTTPException(400, "Укажите характер нагрузки на работе")
    return {
        "age": age, "sex": sex, "height_cm": height_cm, "weight_kg": weight_kg,
        "activity": activity, "occupation": occupation, "smoker": smoker,
    }


@app.post("/api/process")
async def process_scan(
    file: UploadFile = File(...),
    age: int = Form(...),
    sex: str = Form(...),
    height_cm: float = Form(...),
    weight_kg: float = Form(...),
    activity: str = Form(...),
    occupation: str = Form(...),
    smoker: bool = Form(False),
):
    """
    Основной эндпоинт:
    принимает изображение → анализирует → генерирует каркас → возвращает метрики + STL
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Нужно загрузить изображение (PNG, JPG, JPEG)")

    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(400, "Файл слишком большой (макс. 15 МБ)")

    patient = validate_patient(age, sex, height_cm, weight_kg, activity, occupation, smoker)

    # 1. Анализ
    try:
        params = analyze_image(content, patient)
    except Exception as e:
        raise HTTPException(400, f"Не удалось обработать изображение: {str(e)}")

    # 2. Генерация меша
    job_id = str(uuid.uuid4())[:8]
    mesh = generate_gyroid_mesh(
        size=params["approx_size_mm"],
        resolution=22,
        thickness=0.25 + (1 - params["target_porosity"]) * 0.3
    )

    stl_path = None
    mesh_info = {}

    if mesh is not None:
        stl_filename = f"scaffold_{job_id}.stl"
        stl_path = OUTPUT_DIR / stl_filename
        mesh.export(str(stl_path))
        mesh_info = {
            "vertices": len(mesh.vertices),
            "faces": len(mesh.faces),
            "volume_mm3": round(float(mesh.volume), 1) if mesh.is_volume else None,
            "stl_url": f"/api/download/{stl_filename}",
        }
    else:
        mesh_info = {"error": "Не удалось сгенерировать 3D-модель (нет trimesh/skimage)"}

    # 3. Ответ
    return {
        "status": "ok",
        "job_id": job_id,
        "message": "Прототип пайплайна отработал. Это упрощённая демонстрация.",
        "analysis": params,
        "material_recommendation": {
            "material": params["material"],
            "structure": "Gyroid / TPMS",
            "print_method": "FDM",
            "notes": "Рекомендация на основе эвристик. Для реального использования нужна клиническая валидация."
        },
        "metrics": {
            "young_modulus_mpa": params["young_modulus_mpa"],
            "porosity_target": params["target_porosity"],
            "fit_error_mm": params["fit_error_mm"],
            "design_time_sec": 8 + int(params["area_ratio"] * 20),
            "bmi": params["factors"]["bmi"],
            "load_index": params["factors"]["load_index"],
            "bone_quality_index": params["factors"]["bone_quality_index"],
        },
        "adjustments": params["notes"],
        "mesh": mesh_info,
        "disclaimer": "Research Use Only. Это ранний прототип студенческого проекта. Результаты приближённые."
    }


_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        ctx = mp.get_context("spawn")
        try:
            _pool = ProcessPoolExecutor(max_workers=1, mp_context=ctx, max_tasks_per_child=1)
        except TypeError:  # Python < 3.11
            _pool = ProcessPoolExecutor(max_workers=1, mp_context=ctx)
    return _pool


def _reset_pool():
    global _pool
    try:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
    finally:
        _pool = None


MAX_CT_BYTES = 150 * 1024 * 1024


@app.post("/api/process_ct")
async def process_ct(
    file: UploadFile = File(...),
    age: int = Form(...),
    sex: str = Form(...),
    height_cm: float = Form(...),
    weight_kg: float = Form(...),
    activity: str = Form(...),
    occupation: str = Form(...),
    smoker: bool = Form(False),
    simulate_defect: bool = Form(False),
    porosity_pct: float | None = Form(None),
):
    """КТ черепа (zip с DICOM или NIfTI) → каркас-имплант по форме дефекта свода → STL."""
    patient = validate_patient(age, sex, height_cm, weight_kg, activity, occupation, smoker)

    f = file.file
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > MAX_CT_BYTES:
        raise HTTPException(400, "Файл слишком большой (макс. 150 МБ). Уменьшите серию или сожмите NIfTI (.nii.gz).")
    if size < 1024:
        raise HTTPException(400, "Файл пустой или повреждён")

    factors = patient_factors(patient)
    notes = []

    # Пористость: для свода черепа базово 65%, выше нагрузка — ниже пористость
    porosity = float(np.clip(0.65 - 0.15 * (factors["load_index"] - 1.0), 0.45, 0.85))
    if factors["load_index"] > 1.05:
        notes.append(f"Повышенная нагрузка (индекс {factors['load_index']}): пористость каркаса снижена до {round(porosity * 100)}%")
    elif factors["load_index"] < 0.95:
        notes.append(f"Пониженная нагрузка (индекс {factors['load_index']}): допустима пористость {round(porosity * 100)}%")

    if porosity_pct is not None:
        # врач задал пористость явно — она важнее расчётной
        porosity = float(np.clip(porosity_pct / 100.0, 0.30, 0.90))
        notes.append(f"Пористость задана вручную: {round(porosity * 100)}%")

    young_mpa = 800 - (porosity - 0.5) * 900
    young_mpa *= 0.75 + 0.25 * factors["bone_quality_index"]
    young_mpa = float(np.clip(young_mpa, 300, 950))
    if factors["bone_quality_index"] < 0.85:
        notes.append(f"Оценённое качество кости снижено (индекс {factors['bone_quality_index']}): жёсткость каркаса подбирается мягче")

    if factors["load_index"] > 1.15 or factors["bone_quality_index"] < 0.7:
        material = "PCL + β-ТКФ (композит)"
        notes.append("Рекомендован композит с β-трикальцийфосфатом: лучше интеграция с костью и выше жёсткость")
    else:
        material = "PCL (поликапролактон)"

    # Тяжёлая обработка идёт в отдельном процессе: память полностью освобождается после каждого запроса
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
    try:
        f.seek(0)
        shutil.copyfileobj(f, tmp)
        tmp.close()
        loop = asyncio.get_running_loop()
        stl, info, preview = await loop.run_in_executor(
            _get_pool(), cranio.run_job, tmp.name, file.filename or "", porosity, simulate_defect
        )
    except cranio.CTError as e:
        raise HTTPException(400, str(e))
    except BrokenProcessPool:
        _reset_pool()
        raise HTTPException(400, "Не хватило памяти сервера для этой КТ. Попробуйте файл меньшего размера или повторите запрос.")
    except MemoryError:
        raise HTTPException(400, "Не хватило памяти для обработки. Загрузите КТ меньшего размера.")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(400, f"Не удалось обработать КТ: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    job_id = str(uuid.uuid4())[:8]
    stl_filename = f"cranial_scaffold_{job_id}.stl"
    (OUTPUT_DIR / stl_filename).write_bytes(stl)

    if info["simulated_defect"]:
        notes.insert(0, "Дефект смоделирован автоматически (круг ⌀≈44 мм на макушке): так можно проверить сайт на здоровой КТ")
    notes.append(
        f"Форма пластины построена по восстановленному контуру черепа вокруг дефекта; толщина {info['thickness_mm']} мм взята по соседней кости"
    )

    return {
        "status": "ok",
        "mode": "ct_skull",
        "job_id": job_id,
        "message": "Каркас построен по КТ.",
        "ct": {
            "defect_size_mm": [round(info["size_u_mm"]), round(info["size_v_mm"])],
            "defect_area_cm2": round(info["area_mm2"] / 100.0, 1),
            "thickness_mm": info["thickness_mm"],
            "simulated_defect": info["simulated_defect"],
            "source_spacing_mm": info["source_spacing_mm"],
        },
        "material_recommendation": {
            "material": material,
            "structure": "Gyroid / TPMS, по форме дефекта",
            "print_method": "FDM / SLS",
            "notes": "Рекомендация на основе эвристик. Для реального использования нужна клиническая валидация.",
        },
        "metrics": {
            "young_modulus_mpa": round(young_mpa),
            "porosity_target": round(porosity, 2),
            "porosity_actual": round(info["porosity_actual"], 2),
            "fit_error_mm": None,
            "design_time_sec": None,
            "bmi": factors["bmi"],
            "load_index": factors["load_index"],
            "bone_quality_index": factors["bone_quality_index"],
            "implant_volume_cm3": round(info["mesh_volume_mm3"] / 1000.0, 2),
            "implant_mass_g": round(info["mesh_volume_mm3"] * 0.00114, 1),
        },
        "adjustments": notes,
        "mesh": {
            "vertices": info["vertices"],
            "faces": info["faces"],
            "volume_mm3": round(info["mesh_volume_mm3"], 1),
            "stl_mb": info["stl_mb"],
            "stl_url": f"/api/download/{stl_filename}",
        },
        "preview": preview,
        "disclaimer": "Research Use Only. Прототип: форма построена автоматически и не проверена врачом; не для клинического применения.",
    }


API_KEY = os.environ.get("IMPLANT_API_KEY", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


@app.get("/api/v1/health")
async def v1_health():
    return {"status": "ok", "service": "neuroforge-implant"}


@app.post("/api/v1/ct-implant")
async def v1_ct_implant(
    request: Request,
    file: UploadFile = File(...),
    porosity_pct: float | None = Form(None),
    simulate_defect: bool = Form(False),
    age: int | None = Form(None),
    sex: str | None = Form(None),
    height_cm: float | None = Form(None),
    weight_kg: float | None = Form(None),
    activity: str | None = Form(None),
    occupation: str | None = Form(None),
    smoker: bool = Form(False),
):
    """
    Вход для мобильного приложения: КТ черепа + пористость от врача (+ необязательные данные пациента).
    Не указанные данные пациента заменяются средними значениями. Возвращает то же, что /api/process_ct,
    но со ссылкой на STL в виде полного адреса.
    """
    if API_KEY and request.headers.get("x-api-key", "") != API_KEY:
        raise HTTPException(401, "Нет доступа: неверный ключ сервиса")

    notes_default = []
    if age is None or sex is None or height_cm is None or weight_kg is None:
        notes_default.append("Данные пациента неполные: для остальных параметров использованы средние значения")
    result = await process_ct(
        file=file,
        age=age if age is not None else 40,
        sex=sex if sex in ("male", "female") else "male",
        height_cm=height_cm if height_cm is not None else 170.0,
        weight_kg=weight_kg if weight_kg is not None else 70.0,
        activity=activity if activity in ACTIVITY_FACTOR else "moderate",
        occupation=occupation if occupation in OCCUPATION_FACTOR else "mixed",
        smoker=smoker,
        simulate_defect=simulate_defect,
        porosity_pct=porosity_pct,
    )
    result["adjustments"] = notes_default + result.get("adjustments", [])
    base = PUBLIC_BASE_URL or (
        f"{request.headers.get('x-forwarded-proto', request.url.scheme)}://{request.headers.get('host', request.url.netloc)}"
    )
    result["mesh"]["stl_url"] = base + result["mesh"]["stl_url"]
    return result


@app.get("/api/download/{filename}")
async def download_stl(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists() or ".." in filename:
        raise HTTPException(404, "Файл не найден")
    return FileResponse(
        path,
        media_type="application/sla",
        filename=filename
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "skimage": HAS_SKIMAGE,
        "trimesh": HAS_TRIMESH,
    }


# Раздача статики (фронтенд)
STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
