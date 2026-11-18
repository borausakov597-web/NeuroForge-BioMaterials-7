import { Platform, StyleSheet, View } from 'react-native';
import { WebView } from 'react-native-webview';
import { theme } from '@/lib/theme';
import { MESH_VIEWER_HTML } from './meshViewerHtml';

/** Интерактивный 3D-просмотр STL: вращение, масштаб, подложка-череп. */
export function MeshViewer3D({ stlUrl, skull, height = 340 }: { stlUrl: string; skull?: number[][]; height?: number }) {
  const init = JSON.stringify({ stlUrl, skull: skull ?? null }).replace(/</g, '\\u003c');
  const html = MESH_VIEWER_HTML.replace('/*__INIT__*/', `window.NF_INIT=${init};`);
  return (
    <View style={[styles.box, { height }]}>
      {Platform.OS === 'web' ? (
        // @ts-expect-error iframe — элемент только для web
        <iframe srcDoc={html} style={{ border: 0, width: '100%', height: '100%' }} title="3D" />
      ) : (
        <WebView
          originWhitelist={['*']}
          source={{ html }}
          style={styles.web}
          javaScriptEnabled
          scrollEnabled={false}
          overScrollMode="never"
          mixedContentMode="always"
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { borderRadius: theme.radius.lg, overflow: 'hidden', backgroundColor: '#F1F5F9', borderWidth: 1, borderColor: theme.colors.border },
  web: { flex: 1, backgroundColor: 'transparent' },
});
