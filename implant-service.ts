// Клиент Python-сервиса генерации имплантов по КТ (backend/app.py, эндпоинт /api/v1/ct-implant).
// Cloudflare Worker не умеет считать КТ, поэтому сервис хостится отдельно (например, Amvera).
// Настройка: EXPO_PUBLIC_IMPLANT_API_URL=https://ваш-сервис.amvera.io  (без слэша на конце)
//            EXPO_PUBLIC_IMPLANT_API_KEY=...  (необязательно, если на сервисе задан IMPLANT_API_KEY)

export const IMPLANT_API_URL = (process.env.EXPO_PUBLIC_IMPLANT_API_URL ?? '').replace(/\/+$/, '');
const IMPLANT_API_KEY = process.env.EXPO_PUBLIC_IMPLANT_API_KEY ?? '';

export interface PatientInput {
  age?: number;
  sex?: 'male' | 'female';
  height_cm?: number;
  weight_kg?: number;
  activity?: string;
  occupation?: string;
  smoker?: boolean;
}

export interface CtPick {
  uri: string;
  name: string;
  file?: File; // на web
  size?: number;
}

export interface ImplantResult {
  mesh: { stl_url: string; [k: string]: unknown };
  preview?: { skull_points?: number[][] };
  metrics?: { porosity_actual?: number; implant_volume_cm3?: number; implant_mass_g?: number; young_modulus_mpa?: number };
  ct?: { defect_size_mm?: number[]; defect_area_cm2?: number; thickness_mm?: number };
  adjustments?: string[];
  disclaimer?: string;
  [k: string]: unknown;
}

export class ImplantServiceError extends Error {}

export function implantServiceConfigured(): boolean {
  return IMPLANT_API_URL.length > 0;
}

function headers(): Record<string, string> {
  return IMPLANT_API_KEY ? { 'X-API-Key': IMPLANT_API_KEY } : {};
}

export async function generateImplantFromCt(
  ct: CtPick,
  opts: { porosityPct?: number; simulateDefect?: boolean; patient?: PatientInput },
): Promise<ImplantResult> {
  if (!implantServiceConfigured()) {
    throw new ImplantServiceError('Не задан адрес сервиса: добавьте EXPO_PUBLIC_IMPLANT_API_URL в .env');
  }
  const form = new FormData();
  if (ct.file) {
    form.append('file', ct.file, ct.name);
  } else {
    // React Native: объект {uri,name,type} стримится с диска без загрузки в память.
    form.append('file', { uri: ct.uri, name: ct.name, type: 'application/octet-stream' } as unknown as Blob);
  }
  if (opts.porosityPct != null) form.append('porosity_pct', String(opts.porosityPct));
  form.append('simulate_defect', opts.simulateDefect ? 'true' : 'false');
  const p = opts.patient ?? {};
  if (p.age != null) form.append('age', String(p.age));
  if (p.sex) form.append('sex', p.sex);
  if (p.height_cm != null) form.append('height_cm', String(p.height_cm));
  if (p.weight_kg != null) form.append('weight_kg', String(p.weight_kg));
  if (p.activity) form.append('activity', p.activity);
  if (p.occupation) form.append('occupation', p.occupation);
  if (p.smoker) form.append('smoker', 'true');

  let res: Response;
  try {
    res = await fetch(`${IMPLANT_API_URL}/api/v1/ct-implant`, { method: 'POST', headers: headers(), body: form });
  } catch {
    throw new ImplantServiceError('Сервис не отвечает. Проверьте интернет и адрес сервиса.');
  }
  const text = await res.text();
  let json: any = null;
  try { json = JSON.parse(text); } catch { /* не JSON */ }
  if (!res.ok) {
    const detail = json?.detail ?? json?.error;
    throw new ImplantServiceError(typeof detail === 'string' ? detail : `Ошибка сервиса (HTTP ${res.status})`);
  }
  if (!json?.mesh?.stl_url) throw new ImplantServiceError('Сервис вернул неожиданный ответ');
  return json as ImplantResult;
}
