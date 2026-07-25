# Knowledge Assistant

Ask questions about the indexed documents. Every answer is grounded in the
knowledge base, and each claim carries a source you can click to open the
original page.

**What this assistant can do**

- 🔎 **Search** the knowledge base for relevant passages
- 📄 **Load a whole document** when you ask for a summary or overview
- 🖼️ **Show figures** from the documents next to the text describing them
- ✅ **Verify** its own statements against the sources
- 💬 Suggest follow-up questions after each answer

**Tips**

- Not sure what is indexed? Ask which documents are available.
- Name a document ("summarize the Kage 2018 paper") to scope the answer to it.
- Use ⚙️ to switch the chat model or to view and edit the system prompt.

If something is not covered by the documents, the assistant says so instead of
guessing.

---

![KI-Servicezentrum Berlin-Brandenburg](/public/logo_kisz.png)

![Bundesministerium für Forschung, Technologie und Raumfahrt](/public/logo_bmftr.png)

Developed at the **KI-Servicezentrum Berlin-Brandenburg**, funded by the German
Federal Ministry of Research, Technology and Space (grant 01IS22092).

*This screen is `apps/chainlit/chainlit.md` — replace it with your own welcome
text and swap the logos in `public/`. The instance itself is configured in a
`rag.config.yaml`; see the project documentation.*
