import { useState } from 'react';
import { ActivityIndicator, Linking, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { router } from 'expo-router';
import { theme } from '@/lib/theme';
import { ScreenFrame } from '@/components/app/ScreenFrame';
import { BackControl, PrimaryButton, SecondaryButton, StatCell } from '@/components/app/Controls';
import { MeshViewer3D } from '@/components/app/MeshViewer3D';
import { pickCtFile } from '@/lib/ct-picker';
import {
  generateImplantFromCt,
  implantServiceConfigured,
  type CtPick,
  type ImplantResult,
} from '@/lib/implant-service';

const fmt = (v: unknown, unit = '') => (typeof v === 'number' ? `${Math.round(v * 10) / 10}${unit}` : '—');

export default function CtImplantScreen() {
  const [ct, setCt] = useState<CtPick | null>(null);
  const [simulate, setSimulate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImplantResult | null>(null);

  const choose = async () => {
    setError(null);
    try {
      const f = await pickCtFile();
      if (f) { setCt(f); setResult(null); }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось выбрать файл');
    }
  };

  const run = async () => {
    if (!ct) return;
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await generateImplantFromCt(ct, { simulateDefect: simulate }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось построить модель');
    } finally {
      setBusy(false);
    }
  };

  const m = result?.metrics;
  const c = result?.ct;

  return (
    <ScreenFrame title="3D-модель по КТ" back={<BackControl onPress={() => router.back()} />}>
      <ScrollView contentContainerStyle={styles.content}>
        {!implantServiceConfigured() && (
          <Text style={styles.error}>Сервис расчёта не подключён: задайте EXPO_PUBLIC_IMPLANT_API_URL в .env.</Text>
        )}
        <Text style={styles.muted}>
          Загрузите КТ черепа (.nii, .nii.gz или DICOM в .zip). Сервис найдёт дефект кости и построит пористый имплант под его форму.
          Результат — STL для печати. Только для исследований, не клиническое изделие.
        </Text>
        <SecondaryButton label={ct ? `Файл: ${ct.name}` : 'Выбрать файл КТ'} onPress={choose} disabled={busy} />
        <View style={styles.row}>
          <Text style={styles.text}>Вырезать тестовый дефект (для здорового черепа)</Text>
          <Switch value={simulate} onValueChange={setSimulate} disabled={busy} />
        </View>
        <PrimaryButton label={busy ? 'Считаем…' : 'Построить имплант'} onPress={run} disabled={!ct || !implantServiceConfigured()} loading={busy} />
        {busy && (
          <View style={styles.row}>
            <ActivityIndicator color={theme.colors.primary} />
            <Text style={styles.muted}>Обработка КТ занимает 1–3 минуты…</Text>
          </View>
        )}
        {error && <Text style={styles.error}>{error}</Text>}
        {result && (
          <View style={styles.gap}>
            <MeshViewer3D stlUrl={result.mesh.stl_url} skull={result.preview?.skull_points} />
            <View style={styles.row}>
              <StatCell label="Пористость" value={fmt(m?.porosity_actual != null ? m.porosity_actual * 100 : null, '%')} />
              <StatCell label="Объём" value={fmt(m?.implant_volume_cm3, ' см³')} />
              <StatCell label="Дефект" value={c?.defect_size_mm ? `${c.defect_size_mm[0]}×${c.defect_size_mm[1]} мм` : '—'} />
            </View>
            {result.adjustments?.map((n, i) => (
              <Text key={i} style={styles.muted}>• {n}</Text>
            ))}
            <Text style={styles.muted}>{result.disclaimer}</Text>
            <SecondaryButton label="Скачать STL" onPress={() => Linking.openURL(result.mesh.stl_url)} />
          </View>
        )}
      </ScrollView>
    </ScreenFrame>
  );
}

const styles = StyleSheet.create({
  content: { padding: theme.spacing(4), gap: theme.spacing(3) },
  gap: { gap: theme.spacing(3) },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: theme.spacing(3) },
  text: { ...theme.type.body, color: theme.colors.text, flex: 1 },
  muted: { ...theme.type.caption, color: theme.colors.textMuted },
  error: { ...theme.type.body, color: theme.colors.danger },
});
