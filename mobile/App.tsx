import { useState } from "react";
import { SafeAreaView, ScrollView, Text, Pressable, StyleSheet } from "react-native";
import type { ModelProgressUpdate } from "@qvac/sdk";

type Status = "idle" | "downloading" | "loading" | "asking" | "ready" | "error";

export default function App() {
  const [status, setStatus] = useState<Status>("idle");
  const [pct, setPct] = useState(0);
  const [answer, setAnswer] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  async function runSmokeTest() {
    setErrorMsg("");
    setAnswer("");
    try {
      // Cargamos @qvac/sdk (y react-native-bare-kit) solo aqui, al presionar
      // el boton, en vez de arriba del archivo. Asi, si el modulo nativo de
      // bare-kit falla al inicializarse, el error queda atrapado por este
      // try/catch en vez de tumbar toda la app apenas arranca.
      const { completion, downloadAsset, loadModel, LLAMA_3_2_1B_INST_Q4_0, VERBOSITY } =
        await import("@qvac/sdk");

      setStatus("downloading");
      await downloadAsset({
        assetSrc: LLAMA_3_2_1B_INST_Q4_0,
        onProgress: (p: ModelProgressUpdate) => setPct(Math.round(p.percentage)),
      });

      setStatus("loading");
      const modelId = await loadModel({
        modelSrc: LLAMA_3_2_1B_INST_Q4_0,
        modelType: "llm",
        modelConfig: { device: "gpu", ctx_size: 2048, verbosity: VERBOSITY.ERROR },
        onProgress: (p: ModelProgressUpdate) => setPct(Math.round(p.percentage)),
      });

      setStatus("asking");
      const result = completion({
        modelId,
        history: [{ role: "user", content: "Responde en una sola frase: ¿qué es QVAC?" }],
        stream: true,
      });

      let acc = "";
      for await (const token of result.tokenStream) {
        acc += token;
        setAnswer(acc);
      }
      setStatus("ready");
    } catch (err: any) {
      console.error("QVAC smoke test failed:", err);
      setErrorMsg(String(err?.message ?? err));
      setStatus("error");
    }
  }

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>Smoke test QVAC</Text>
        <Text style={styles.status}>Estado: {status}{status === "downloading" || status === "loading" ? ` (${pct}%)` : ""}</Text>
        <Pressable style={styles.button} onPress={runSmokeTest}>
          <Text style={styles.buttonText}>Probar QVAC</Text>
        </Pressable>
        {answer ? <Text style={styles.answer}>{answer}</Text> : null}
        {errorMsg ? <Text style={styles.error}>{errorMsg}</Text> : null}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#fff" },
  content: { padding: 20, gap: 16 },
  title: { fontSize: 22, fontWeight: "600" },
  status: { fontSize: 14, color: "#555" },
  button: { backgroundColor: "#3b6fd4", padding: 14, borderRadius: 10, alignItems: "center" },
  buttonText: { color: "#fff", fontWeight: "600" },
  answer: { fontSize: 16, lineHeight: 22 },
  error: { fontSize: 14, color: "#b5514a" },
});
