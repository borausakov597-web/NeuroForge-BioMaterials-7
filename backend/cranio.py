"""
NeuroForge BioMaterials — каркас-имплант свода черепа по КТ (демо-прототип).

Конвейер:
  1. читаем КТ (zip с DICOM или NIfTI .nii/.nii.gz), приводим к сетке 2 мм;
  2. выделяем кость (порог по HU), берём самый большой связный фрагмент (череп);
  3. находим дефект свода: морфологическое замыкание кости минус сама кость
     (или моделируем тестовый круглый дефект, если снимок здоровый);
  4. строим пластину по форме дефекта: толщина ≈ толщина соседней кости,
     поверхность повторяет внешний контур черепа;
  5. заполняем пластину гироидной решёткой с заданной пористостью + сплошной ободок;
  6. строим поверхность (marching cubes) и отдаём STL в миллиметрах.

Это исследовательский прототип, не медицинское изделие.
"""

import gzip
import io
import os
import struct
import zipfile

import numpy as np
from scipy import ndimage as ndi

# ---- параметры -------------------------------------------------------------
VOXEL_MM = 2.0            # рабочая сетка для поиска дефекта
BONE_HU = 250             # порог кости (на сетке 2 мм: часть вокселей — смесь с мягкими тканями)
CLOSE_R_MM = 30.0         # радиус замыкания: перекрывает дефекты шириной до ~55 мм
VAULT_DEPTH_MM = 90.0     # «свод» — верхние 90 мм черепа от макушки
HEAD_HEIGHT_MM = 220.0    # сколько мм вниз от макушки оставляем в объёме
MIN_DEFECT_CM2 = 6.0      # меньше — считаем, что дефекта нет
SIM_DEFECT_R_MM = 22.0    # радиус тестового дефекта (≈ ⌀44 мм)
FINE_VOXEL_MM = 0.8       # шаг сетки для решётки
LATTICE_PERIOD_MM = 5.0   # период гироида
RIM_MM = 1.6              # ширина сплошного ободка по краю пластины
MAX_FINE_VOXELS = 3_500_000


class CTError(Exception):
    """Ошибка, понятная пользователю."""


# ---- чтение КТ -------------------------------------------------------------
def _block_mean(a, fy, fx):
    h, w = a.shape
    h2, w2 = (h // fy) * fy, (w // fx) * fx
    a = a[:h2, :w2].reshape(h2 // fy, fy, w2 // fx, fx).mean(axis=(1, 3))
    return np.clip(a, -1024, 3071).astype(np.int16)


def _factor(px_mm, target=VOXEL_MM):
    return max(1, int(round(target / max(px_mm, 1e-3))))


_NII_DTYPES = {
    2: np.uint8, 4: np.int16, 8: np.int32, 16: np.float32, 64: np.float64,
    256: np.int8, 512: np.uint16, 768: np.uint32,
}


def _read_nifti(f):
    head = f.read(2)
    f.seek(0)
    raw = gzip.GzipFile(fileobj=f) if head == b"\x1f\x8b" else f

    hdr = raw.read(348)
    if len(hdr) < 348:
        raise CTError("Файл NIfTI повреждён (слишком короткий заголовок)")
    e = "<"
    if struct.unpack("<i", hdr[:4])[0] != 348:
        e = ">"
        if struct.unpack(">i", hdr[:4])[0] != 348:
            raise CTError("Это не файл NIfTI-1 (.nii / .nii.gz)")

    dim = struct.unpack(e + "8h", hdr[40:56])
    datatype = struct.unpack(e + "h", hdr[70:72])[0]
    pixdim = struct.unpack(e + "8f", hdr[76:108])
    vox_offset = struct.unpack(e + "f", hdr[108:112])[0]
    slope, inter = struct.unpack(e + "2f", hdr[112:120])
    qform_code, sform_code = struct.unpack(e + "2h", hdr[252:256])
    qb, qc, qd, qx, qy, qz = struct.unpack(e + "6f", hdr[256:280])
    srow = np.array(struct.unpack(e + "12f", hdr[280:328])).reshape(3, 4)

    if dim[0] < 3:
        raise CTError("В файле NIfTI нет 3D-объёма")
    nx, ny, nz = dim[1], dim[2], dim[3]
    if datatype not in _NII_DTYPES:
        raise CTError(f"Неподдерживаемый тип данных NIfTI ({datatype})")
    dtype = np.dtype(_NII_DTYPES[datatype]).newbyteorder(e)
    if nx * ny * nz == 0:
        raise CTError("Пустой объём NIfTI")

    # матрица ориентации voxel -> мир
    if sform_code > 0:
        A = srow[:, :3]
    elif qform_code > 0:
        a2 = 1.0 - (qb * qb + qc * qc + qd * qd)
        a = np.sqrt(a2) if a2 > 0 else 0.0
        R = np.array([
            [a*a + qb*qb - qc*qc - qd*qd, 2*(qb*qc - a*qd), 2*(qb*qd + a*qc)],
            [2*(qb*qc + a*qd), a*a + qc*qc - qb*qb - qd*qd, 2*(qc*qd - a*qb)],
            [2*(qb*qd - a*qc), 2*(qc*qd + a*qb), a*a + qd*qd - qc*qc - qb*qb],
        ])
        qfac = -1.0 if pixdim[0] < 0 else 1.0
        A = R @ np.diag([pixdim[1], pixdim[2], pixdim[3] * qfac])
    else:
        A = np.diag([pixdim[1] or 1.0, pixdim[2] or 1.0, pixdim[3] or 1.0])

    axes = np.argmax(np.abs(A), axis=0)
    if list(axes) != [0, 1, 2]:
        raise CTError("Нестандартная ориентация NIfTI (не аксиальная). Сохраните КТ в аксиальной ориентации.")
    signs = [1 if A[axes[i], i] >= 0 else -1 for i in range(3)]
    sp = [float(np.linalg.norm(A[:, i])) or 1.0 for i in range(3)]  # x, y, z

    skip = int(vox_offset) - 348
    if skip > 0:
        raw.read(skip)

    fy, fx = _factor(sp[1]), _factor(sp[0])
    slab = nx * ny * dtype.itemsize
    use_scale = (slope not in (0.0, 1.0)) or inter != 0.0
    slices = []
    for _ in range(nz):
        buf = raw.read(slab)
        if len(buf) < slab:
            raise CTError("Файл NIfTI обрезан (данных меньше, чем указано в заголовке)")
        arr = np.frombuffer(buf, dtype=dtype).reshape(ny, nx).astype(np.float32)
        if use_scale:
            arr = arr * (slope if slope != 0 else 1.0) + inter
        slices.append(_block_mean(arr, fy, fx))
    vol = np.stack(slices)  # (z, y, x)
    if signs[2] < 0:
        vol = vol[::-1]
    if signs[1] < 0:
        vol = vol[:, ::-1]
    if signs[0] < 0:
        vol = vol[:, :, ::-1]
    return np.ascontiguousarray(vol), (sp[2], sp[1] * fy, sp[0] * fx)


def _read_dicom_zip(f):
    try:
        import pydicom
    except ImportError:
        raise CTError("На сервере не установлена библиотека pydicom")

    try:
        zf = zipfile.ZipFile(f)
    except zipfile.BadZipFile:
        raise CTError("Не удалось открыть zip-архив")

    groups = {}
    failed = 0
    for info in zf.infolist():
        base = os.path.basename(info.filename)
        if info.is_dir() or not base or base.startswith(".") or "__MACOSX" in info.filename:
            continue
        try:
            with zf.open(info) as fh:
                ds = pydicom.dcmread(io.BytesIO(fh.read()), force=True)
            if not hasattr(ds, "PixelData"):
                continue
            arr = ds.pixel_array.astype(np.float32)
        except Exception:
            failed += 1
            continue
        if arr.ndim != 2:
            continue
        slope = float(getattr(ds, "RescaleSlope", 1) or 1)
        inter = float(getattr(ds, "RescaleIntercept", 0) or 0)
        arr = arr * slope + inter

        ps = getattr(ds, "PixelSpacing", [1.0, 1.0])
        py, px = float(ps[0]), float(ps[1])
        fy, fx = _factor(py), _factor(px)

        ipp = np.array([float(v) for v in getattr(ds, "ImagePositionPatient", [0, 0, 0])])
        iop = [float(v) for v in getattr(ds, "ImageOrientationPatient", [1, 0, 0, 0, 1, 0])]
        n = np.cross(np.array(iop[:3]), np.array(iop[3:]))
        if n[2] < 0:
            n = -n
        pos = float(ipp @ n) if np.any(ipp) else float(getattr(ds, "InstanceNumber", 0))

        key = (str(getattr(ds, "SeriesInstanceUID", "")), arr.shape)
        g = groups.setdefault(key, {"slices": [], "py": py * fy, "px": px * fx})
        g["slices"].append((pos, _block_mean(arr, fy, fx)))

    if not groups:
        raise CTError("В архиве не найдено DICOM-срезов" + (f" (не прочитано файлов: {failed})" if failed else ""))
    g = max(groups.values(), key=lambda v: len(v["slices"]))
    sl = sorted(g["slices"], key=lambda t: t[0])
    if len(sl) < 20:
        raise CTError(f"Слишком мало срезов в серии ({len(sl)}). Нужен полный 3D-объём головы.")
    pos = np.array([p for p, _ in sl])
    dz = float(np.median(np.abs(np.diff(pos)))) if len(pos) > 1 else 0.0
    if dz <= 0:
        raise CTError("Не удалось определить шаг между срезами")
    vol = np.stack([a for _, a in sl])
    return vol, (dz, g["py"], g["px"])


def load_ct(fobj, filename=""):
    fobj.seek(0)
    magic = fobj.read(4)
    fobj.seek(0)
    name = (filename or "").lower()
    if magic[:2] == b"PK":
        return _read_dicom_zip(fobj)
    if name.endswith((".nii", ".nii.gz")) or magic[:2] == b"\x1f\x8b" or struct.unpack("<i", magic.ljust(4, b"\0"))[0] == 348:
        return _read_nifti(fobj)
    raise CTError("Загрузите zip-архив с DICOM-срезами или файл NIfTI (.nii / .nii.gz)")


# ---- геометрия -------------------------------------------------------------
def to_iso(vol, spacing, target=VOXEL_MM):
    zoom = [s / target for s in spacing]
    if all(abs(z - 1) < 0.02 for z in zoom):
        return vol
    out = ndi.zoom(vol.astype(np.float32), zoom, order=1)
    return out.astype(np.int16)


def segment_skull(iso):
    """Кость → самый большой фрагмент → обрезка до головы. Возвращает (bone, zmax_idx)."""
    bone = iso > BONE_HU
    lab, n = ndi.label(bone)
    if n == 0:
        raise CTError("Кость не найдена. Проверьте, что это КТ в единицах Хаунсфилда (HU).")
    sizes = ndi.sum(bone, lab, index=np.arange(1, n + 1))
    main = lab == (1 + int(np.argmax(sizes)))
    zs = np.where(main.any(axis=(1, 2)))[0]
    zmax = int(zs.max())
    z0 = max(0, zmax - int(HEAD_HEIGHT_MM / VOXEL_MM))
    main = main[z0:zmax + 1]
    ys, xs = np.where(main.any(axis=0))
    m = 6
    y0, y1 = max(0, ys.min() - m), min(main.shape[1], ys.max() + 1 + m)
    x0, x1 = max(0, xs.min() - m), min(main.shape[2], xs.max() + 1 + m)
    main = main[:, y0:y1, x0:x1]
    w_mm = (xs.max() - xs.min() + 1) * VOXEL_MM
    d_mm = (ys.max() - ys.min() + 1) * VOXEL_MM
    if min(w_mm, d_mm) < 90:
        raise CTError("Не найден череп: загрузите КТ головы (ожидается ширина головы > 90 мм).")
    return np.ascontiguousarray(main)


def _cast(boneF, c0, dirs, r0=20.0, r1=160.0, step=1.0, chunk=6000):
    """Бросаем лучи из центра черепа; возвращаем булев массив «кость на луче» (N, R)."""
    r = np.arange(r0, r1 + 1e-6, step)
    out = np.zeros((len(dirs), len(r)), dtype=bool)
    for i in range(0, len(dirs), chunk):
        d = dirs[i:i + chunk]
        pts = c0[None, None, :] + d[:, None, :] * r[None, :, None]
        idx = (pts / VOXEL_MM).reshape(-1, 3).T
        vals = ndi.map_coordinates(boneF, idx, order=1, mode="constant", cval=0.0)
        out[i:i + chunk] = vals.reshape(len(d), len(r)) > 0.5
    return out, r


def _outer_idx(hits):
    has = hits.any(axis=1)
    idx = hits.shape[1] - 1 - np.argmax(hits[:, ::-1], axis=1)
    return has, idx


def _frame(d):
    helper = np.array([0.0, 0.0, 1.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(d, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(d, e1)
    return e1, e2


def skull_center(bone):
    v = VOXEL_MM
    top = (bone.shape[0] - 1) * v
    zi, yi, xi = np.where(bone[max(0, bone.shape[0] - 1 - int(VAULT_DEPTH_MM / v)):])
    c0 = np.array([top - 80.0, (yi.min() + yi.max()) / 2.0 * v, (xi.min() + xi.max()) / 2.0 * v])
    return c0


def carve_test_defect(bone):
    v = VOXEL_MM
    zt = int(np.where(bone.any(axis=(1, 2)))[0].max())
    ys, xs = np.where(bone[zt])
    cy, cx = float(ys.mean()), float(xs.mean())
    cz = zt - 5.0 / v
    zz, yy, xx = np.ogrid[:bone.shape[0], :bone.shape[1], :bone.shape[2]]
    sphere = ((zz - cz) ** 2 + (yy - cy) ** 2 + (xx - cx) ** 2) * v * v <= SIM_DEFECT_R_MM ** 2
    return bone & ~sphere


def detect_defect(boneF, c0):
    """Грубый поиск: лучи по своду; нет кости на луче = дефект. Возвращает (ось d, угловой радиус)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    th = np.deg2rad(np.linspace(0.0, 75.0, 51))
    ph = np.deg2rad(np.arange(0.0, 360.0, 2.0))
    dth, dph = th[1] - th[0], ph[1] - ph[0]
    dirs, w = [], []
    for t in th:
        if t == 0.0:
            dirs.append([1.0, 0.0, 0.0])
            w.append(np.pi * (dth / 2) ** 2)
        else:
            for p in ph:
                dirs.append([np.cos(t), np.sin(t) * np.sin(p), np.sin(t) * np.cos(p)])
                w.append(np.sin(t) * dth * dph)
    dirs = np.array(dirs)
    w = np.array(w)
    hits, _ = _cast(boneF, c0, dirs)
    miss = ~hits.any(axis=1)
    if not miss.any():
        raise CTError("Дефект свода черепа не найден. Если это здоровая КТ, включите «Смоделировать дефект».")
    md, mw = dirs[miss], w[miss]
    pairs = cKDTree(md).query_pairs(r=0.07, output_type="ndarray")
    n = len(md)
    if len(pairs):
        g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
        _, lab = connected_components(g, directed=False)
    else:
        lab = np.arange(n)
    areas = np.bincount(lab, weights=mw) * 75.0 ** 2
    k = int(np.argmax(areas))
    if areas[k] < MIN_DEFECT_CM2 * 100.0:
        raise CTError("Дефект свода черепа не найден (слишком малая область). Для проверки включите «Смоделировать дефект».")
    cd = md[lab == k]
    d = cd.mean(axis=0)
    d /= np.linalg.norm(d)
    a_h = float(np.max(np.arccos(np.clip(cd @ d, -1, 1))))
    return d, a_h, float(areas[k])


def build_scaffold(boneF, c0, d, a_h, porosity):
    """Пластина по восстановленному контуру черепа + гироид → (verts_zyx_mm, faces, info)."""
    from scipy.interpolate import RBFInterpolator

    vx = FINE_VOXEL_MM
    e1, e2 = _frame(d)
    rho0 = 70.0
    d_al = 0.5 * vx / rho0
    d_be = 0.01
    alpha = np.arange(0.0, min(a_h + 0.45, 1.3), d_al)
    beta = np.arange(0.0, 2 * np.pi, d_be)
    A, B = np.meshgrid(alpha, beta, indexing="ij")
    dirs = (np.cos(A)[..., None] * d + np.sin(A)[..., None] * (np.cos(B)[..., None] * e1 + np.sin(B)[..., None] * e2))
    flat = dirs.reshape(-1, 3)
    hits, rr = _cast(boneF, c0, flat)
    has, idx = _outer_idx(hits)
    has = has.reshape(A.shape)
    r_out = rr[idx].reshape(A.shape)
    miss = ~has

    # дыра: «нет кости» рядом с осью дефекта
    hole = miss & (A <= a_h + 0.06)
    hole = ndi.binary_fill_holes(hole)
    if hole.sum() < 50:
        raise CTError("Не удалось выделить область дефекта")

    # край дыры по каждому направлению beta
    edge = np.where(hole.any(axis=0), (hole * A).max(axis=0), 0.0)
    ring = has & (A > edge[None, :] + 0.03) & (A < edge[None, :] + 0.30)
    ri, rj = np.where(ring)
    if len(ri) < 30:
        raise CTError("Вокруг дефекта слишком мало кости для восстановления контура")
    sel = np.random.default_rng(0).choice(len(ri), min(len(ri), 1200), replace=False)
    ri, rj = ri[sel], rj[sel]
    pts_ring = np.stack([A[ri, rj] * np.cos(B[ri, rj]), A[ri, rj] * np.sin(B[ri, rj])], axis=1)
    rbf = RBFInterpolator(pts_ring, r_out[ri, rj], kernel="thin_plate_spline", smoothing=1.0)
    hi, hj = np.where(hole)
    pts_hole = np.stack([A[hi, hj] * np.cos(B[hi, hj]), A[hi, hj] * np.sin(B[hi, hj])], axis=1)
    r_fill = rbf(pts_hole)

    # толщина соседней кости: длина непрерывного участка кости на луче от наружной поверхности
    th = []
    for i, j in zip(ri[:300], rj[:300]):
        k = idx.reshape(A.shape)[i, j]
        row = hits.reshape(A.shape + (-1,))[i, j]
        n = 0
        while k - n >= 0 and row[k - n]:
            n += 1
        th.append(n * 1.0)
    thickness = float(np.clip(np.median(th) if th else 6.0, 3.0, 10.0))

    # точки пластины: от (r_fill - T) до r_fill вдоль каждого луча дыры
    steps = np.arange(0.0, thickness + 1e-6, 0.35 * vx)
    dh = dirs[hi, hj]                                        # (M,3)
    rad = r_fill[:, None] - steps[None, :]                   # (M,S)
    P = c0[None, None, :] + dh[:, None, :] * rad[..., None]  # (M,S,3) мм (z,y,x)
    P = P.reshape(-1, 3)
    pad = 3
    O = P.min(axis=0) - pad * vx
    n = np.ceil((P.max(axis=0) - O) / vx).astype(int) + pad + 1
    while int(np.prod(n)) > MAX_FINE_VOXELS:
        vx *= 1.15
        O = P.min(axis=0) - pad * vx
        n = np.ceil((P.max(axis=0) - O) / vx).astype(int) + pad + 1
    ii = np.round((P - O) / vx).astype(int)
    plate = np.zeros(tuple(n), dtype=bool)
    plate[ii[:, 0], ii[:, 1], ii[:, 2]] = True
    plate = ndi.binary_closing(plate, iterations=1) | plate

    # плоская проекция дыры → расстояние до края → ободок
    px = 0.4
    sx = r_fill * A[hi, hj] * np.cos(B[hi, hj])
    sy = r_fill * A[hi, hj] * np.sin(B[hi, hj])
    ix = np.round((sx - sx.min()) / px).astype(int) + 2
    iy = np.round((sy - sy.min()) / px).astype(int) + 2
    F = np.zeros((iy.max() + 3, ix.max() + 3), dtype=bool)
    F[iy, ix] = True
    F = ndi.binary_closing(F, iterations=2)
    dist = ndi.distance_transform_edt(F) * px
    near_edge = dist[iy, ix] <= RIM_MM
    rim = np.zeros_like(plate)
    Pr = c0[None, None, :] + dh[near_edge][:, None, :] * (r_fill[near_edge][:, None] - steps[None, :])[..., None]
    jj = np.round((Pr.reshape(-1, 3) - O) / vx).astype(int)
    rim[jj[:, 0], jj[:, 1], jj[:, 2]] = True
    rim = ndi.binary_closing(rim, iterations=1) | rim
    plate |= rim

    # гироид
    Z = O[0] + np.arange(n[0]) * vx
    Y = O[1] + np.arange(n[1]) * vx
    X = O[2] + np.arange(n[2]) * vx
    ZZ, YY, XX = np.meshgrid(Z, Y, X, indexing="ij")
    k = 2.0 * np.pi / LATTICE_PERIOD_MM
    g = np.sin(k * XX) * np.cos(k * YY) + np.sin(k * YY) * np.cos(k * ZZ) + np.sin(k * ZZ) * np.cos(k * XX)
    del ZZ, YY, XX
    n_plate, n_rim = int(plate.sum()), int((plate & rim).sum())
    # доля решётки в нерамочной части, чтобы с ободком получить целевую пористость
    f_part = ((1.0 - porosity) * n_plate - n_rim) / max(n_plate - n_rim, 1)
    # «сетчатый» гироид (одна из двух взаимопроникающих сеток): стойки толще, чем у листового, и устойчивы к разрешению сетки
    c = float(np.quantile(g[plate & ~rim], float(np.clip(1.0 - f_part, 0.05, 0.95))))
    solid = plate & ((g >= c) | rim)
    actual_por = 1.0 - solid.sum() / plate.sum()

    # неявное поле: >0 внутри материала. Решётка и ободок ограничены пластиной по знаковому расстоянию —
    # поверхность получается гладкой, а толщина стенок точно соответствует заданной пористости
    def sdist(m):
        return (ndi.distance_transform_edt(m) - ndi.distance_transform_edt(~m)) * vx

    f_lat = (g - c) / (k * 1.2)
    f = np.minimum(np.maximum(f_lat, sdist(rim)), sdist(plate))
    f = ndi.gaussian_filter(np.pad(f, 3, mode="constant", constant_values=-3 * vx), 0.5)
    from skimage.measure import marching_cubes
    verts, faces, _, _ = marching_cubes(f, level=0.0, spacing=(vx, vx, vx))
    verts = verts + np.array([Z[0], Y[0], X[0]]) - 3 * vx

    info = {
        "plate_volume_mm3": float(plate.sum() * vx ** 3),
        "solid_volume_mm3": float(solid.sum() * vx ** 3),
        "porosity_actual": float(actual_por),
        "size_u_mm": float((sx.max() - sx.min())),
        "size_v_mm": float((sy.max() - sy.min())),
        "area_mm2": float(F.sum() * px * px),
        "fine_voxel_mm": float(vx),
        "thickness_mm": round(thickness, 1),
    }
    return verts, faces, info


def mesh_volume_mm3(verts, faces):
    t = verts[faces]
    return abs(float(np.sum(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2]))) / 6.0))


def stl_bytes(verts_xyz, faces):
    tri = verts_xyz[faces].astype(np.float32)
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    ln[ln == 0] = 1
    nrm = (nrm / ln).astype(np.float32)
    rec = np.zeros(len(faces), dtype=[("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")])
    rec["n"] = nrm
    rec["v"] = tri.reshape(-1, 9)
    return b"NeuroForge BioMaterials scaffold (mm)".ljust(80, b"\0") + struct.pack("<I", len(faces)) + rec.tobytes()


def skull_preview_points(bone, offset_xyz, limit=30000, seed=1):
    surf = bone & ~ndi.binary_erosion(bone)
    zi, yi, xi = np.where(surf)
    pts = np.stack([xi, yi, zi], axis=1).astype(np.float32) * VOXEL_MM - offset_xyz
    if len(pts) > limit:
        idx = np.random.default_rng(seed).choice(len(pts), limit, replace=False)
        pts = pts[idx]
    return np.round(pts, 1).tolist()


# ---- главный вход ----------------------------------------------------------
def process_ct(fobj, filename, porosity, simulate_defect=False):
    vol, spacing = load_ct(fobj, filename)
    iso = to_iso(vol, spacing)
    del vol
    bone = segment_skull(iso)
    del iso
    simulated = False
    if simulate_defect:
        bone = carve_test_defect(bone)
        simulated = True
    boneF = ndi.binary_closing(bone, iterations=1).astype(np.float32)
    c0 = skull_center(bone)
    d, a_h, area_mm2 = detect_defect(boneF, c0)
    target = porosity
    for attempt in range(2):
        verts_zyx, faces, info = build_scaffold(boneF, c0, d, a_h, target)
        verts_xyz = verts_zyx[:, ::-1].copy()      # (z,y,x) -> (x,y,z)
        faces = faces[:, ::-1].copy()              # смена порядка осей меняет ориентацию граней
        mesh_vol = mesh_volume_mm3(verts_xyz, faces)
        real_por = 1.0 - mesh_vol / info["plate_volume_mm3"]
        if attempt == 0 and abs(real_por - porosity) > 0.03:
            # сглаживание тонких стоек занижает объём: подправляем цель и строим ещё раз
            target = float(np.clip(target + (real_por - porosity) * -1.0, 0.3, 0.9))
            continue
        break
    info["porosity_actual"] = float(real_por)
    offset = verts_xyz.mean(axis=0)
    verts_xyz -= offset
    stl = stl_bytes(verts_xyz, faces)

    info.update({
        "simulated_defect": simulated,
        "defect_angular_area_mm2": round(area_mm2, 0),
        "mesh_volume_mm3": mesh_vol,
        "vertices": int(len(verts_xyz)),
        "faces": int(len(faces)),
        "stl_mb": round(len(stl) / 1e6, 1),
        "source_spacing_mm": [round(float(s), 2) for s in spacing],
    })
    preview = {"skull_points": skull_preview_points(bone, offset.astype(np.float32))}
    return stl, info, preview


def run_job(path, filename, porosity, simulate_defect=False):
    """Точка входа для отдельного процесса: вся тяжёлая память освобождается, когда процесс завершается."""
    with open(path, "rb") as f:
        return process_ct(f, filename, porosity, simulate_defect)
