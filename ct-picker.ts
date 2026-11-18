// Выбор файла КТ (.nii, .nii.gz или DICOM .zip). Свой пикер: заблокированный
// lib/document-picker.ts принимает только PDF, а его трогать нельзя.
import * as DocumentPicker from 'expo-document-picker';
import type { CtPick } from './implant-service';

const OK = /\.(nii|nii\.gz|zip|dcm)$/i;

export async function pickCtFile(): Promise<CtPick | null> {
  try {
    const r = await DocumentPicker.getDocumentAsync({
      type: '*/*', // у .nii нет стандартного MIME, фильтруем по имени ниже
      multiple: false,
      copyToCacheDirectory: true,
      base64: false,
    });
    if (r.canceled || !r.assets?.[0]) return null;
    const a = r.assets[0];
    if (!OK.test(a.name)) throw new Error('Нужен файл .nii, .nii.gz или DICOM в .zip');
    return { uri: a.uri, name: a.name, size: a.size, file: (a as { file?: File }).file };
  } catch (e) {
    if (e instanceof Error && e.message.startsWith('Нужен')) throw e;
    return null;
  }
}
