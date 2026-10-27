# Third-Party Notices

VeriLex original code in v1.0.0 is licensed under PolyForm Noncommercial 1.0.0.
The third-party components below retain their own licenses and copyright notices;
VeriLex does not relicense those components under PolyForm.

## Case calendar desktop reminders (2026-10-06)

VeriLex uses unmodified `notify-rust` 4.11.7 (MIT OR Apache-2.0,
Copyright (c) 2017 Hendrik Sollich) and its locked platform dependencies to
submit generic local calendar notifications. It uses the existing locked
`reqwest` 0.13.4 (MIT OR Apache-2.0) for authenticated loopback requests only.
The macOS adapter is `mac-notification-sys` 0.6.15 (MIT OR Apache-2.0).
The existing locked `rustls` 0.23.43 (Apache-2.0 OR ISC OR MIT) and `ring`
0.17.14 initialize the HTTP client crypto provider shared with the updater.
Python `tzdata` 2026.5 (Apache-2.0; IANA timezone data is public domain) is
bundled unmodified so reminders also work without system timezone files.
Python dependency checksums are recorded in `runtime/uv.lock`.
Dependency resolution and checksums are recorded in `desktop/src-tauri/Cargo.lock`.
License texts are included under `third_party/licenses/case-calendar/` and
in the application bundle's corresponding licenses directory.

- https://github.com/hoodie/notify-rust
- https://github.com/h4llow3En/mac-notification-sys
- https://github.com/seanmonstar/reqwest
- https://github.com/rustls/rustls
- https://github.com/briansmith/ring
- https://github.com/python/tzdata

## pdf.js (pdfjs-dist)

VeriLex bundles the pdfjs-dist npm package to render PDF previews locally
inside the application's WebView. No pdf.js code was modified.

- Project: https://github.com/mozilla/pdf.js
- License: Apache-2.0
- Copyright: Licensed under the Apache License, Version 2.0

```text
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
```

## html5lib

VeriLex bundles html5lib 1.1 to build an HTML5-conformant in-memory element
tree before extracting visible web-page text. VeriLex does not modify html5lib.

- Project: https://github.com/html5lib/html5lib-python
- License: MIT
- Copyright: Copyright (c) 2006-2013 James Graham and other contributors

```text
Copyright (c) 2006-2013 James Graham and other contributors

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## six

html5lib depends on six 1.17.0, which VeriLex bundles unmodified.

- Project: https://github.com/benjaminp/six
- License: MIT
- Copyright: Copyright (c) 2010-2024 Benjamin Peterson

```text
Copyright (c) 2010-2024 Benjamin Peterson

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## webencodings

html5lib depends on webencodings 0.6.1, which VeriLex bundles unmodified.

- Project: https://github.com/CourtBouillon/webencodings
- License: BSD-3-Clause
- Copyright: Copyright (c) 2012, Simon Sapin

```text
BSD 3-Clause License

Copyright (c) 2012, Simon Sapin
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## OpenWorker

VeriLex includes or adapts portions of OpenWorker.

- Project: https://github.com/andrewyng/openworker
- License: MIT
- Copyright: retained from the OpenWorker source files and license notice

The controlled web-search provider adapts the provider abstraction and
DuckDuckGo adapter structure from `coworker/web/providers.py` at commit
`86c57f0692a5a318e55d1b9e0188d798b9fc5690`. VeriLex fixes the endpoint to
DuckDuckGo, uses a cancellable async client that ignores system proxy state,
normalizes results, and maps public errors.

The controlled web reader adapts the public-address validation and connection
pinning design from `coworker/web/guard.py` at the same commit. VeriLex requires
every resolved address to be public unicast, pins HTTP connections to a checked
IP while preserving the original Host and TLS SNI, and revalidates every
redirect before following it.

The skill store adapts the name-validation and safe-write policy from
`coworker/skills/store.py` (studied on the 2026-08-30 research snapshot; see
`docs/provenance/skill-editor.md`): the skill-name whitelist
`^[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9._\-\u4e00-\u9fff]*` (OpenWorker's
ASCII whitelist extended by VeriLex to allow Chinese skill names per the
2026-08-31 product decision) with a 64-character cap and explicit `..` /
separator / space refusal, case-insensitive uniqueness, symlink refusal, and
the same-directory temp file + fsync + atomic replace write discipline.
VeriLex implements these patterns from its own test contracts; the trash
folder, the SQLite disabled-skills table, the error-copy system, and the
always-human `save_skill` approval are VeriLex-specific.

Daily conversation actions in `desktop/src/components/timeline/MessageActions.tsx`
adapt the clipboard-success feedback and transient action state from
`surfaces/gui/src/components/Transcript.tsx` at OpenWorker commit
`5bc10d928e0b64aae74313349a3b17bd19643ae2`. The boundary and bounded user-list
helpers in `runtime/verilex_runtime/agent/compaction.py` adapt
`coworker/compaction.py` at that commit. VeriLex retains whole turns and the
original SQLite history. Copyright (c) 2024 Andrew Ng. MIT.

The OpenWorker MIT license is reproduced below and must remain with redistributed
source and binaries that include adapted OpenWorker portions.

```text
MIT License

Copyright (c) 2024 Andrew Ng

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

## OpenAI Codex

VeriLex references and adapts the macOS Seatbelt sandbox profile structure from
OpenAI Codex.

- Project: https://github.com/openai/codex
- License: Apache License 2.0
- Copyright: retained from the OpenAI Codex source

The macOS Seatbelt profile in `runtime/verilex_runtime/tools/sandbox.py` follows
the closed-by-default `(deny default)` policy and the bootstrap/path carveouts
modeled in Codex's `sandboxing/src/seatbelt.rs`. The Apache License 2.0 is
reproduced below and must remain with redistributed source and binaries that
include adapted Codex portions.

```text
Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by the
      copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all other
      entities that control, are controlled by, or are under common control
      with that entity.

      "You" (or "Your") shall mean an individual or Legal Entity exercising
      permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation source,
      and configuration files.

      "Object" form shall mean any form resulting from mechanical transformation
      or translation of a Source form, including but not limited to compiled
      object code, generated documentation, and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or Object form,
      made available under the License, as indicated by a copyright notice that
      is included in or attached to the work.

      "Derivative Works" shall mean any work, whether in Source or Object form,
      that is based on (or derived from) the Work and for which the editorial
      revisions, annotations, elaborations, or other modifications represent,
      as a whole, an original work of authorship.

      "Contribution" shall mean any work of authorship, including the original
      version of the Work and any modifications or additions to that Work or
      Derivative Works thereof, that is intentionally submitted to Licensor
      for inclusion in the Work by the copyright owner or by an individual or
      Legal Entity authorized to submit on behalf of the copyright owner.

      "Contributor" shall mean Licensor and any individual or Legal Entity on
      behalf of whom a Contribution has been received by Licensor.

   2. Grant of Copyright License. Subject to the terms and conditions of this
      License, each Contributor hereby grants to You a perpetual, worldwide,
      non-exclusive, no-charge, royalty-free, irrevocable copyright license to
      reproduce, prepare Derivative Works of, publicly display, publicly perform,
      sublicense, and distribute the Work and such Derivative Works in Source or
      Object form.

   3. Grant of Patent License. Subject to the terms and conditions of this
      License, each Contributor hereby grants to You a perpetual, worldwide,
      non-exclusive, no-charge, royalty-free, irrevocable patent license to make,
      have made, use, offer to sell, sell, import, and otherwise transfer the Work.

   4. Redistribution. You may reproduce and distribute copies of the Work or
      Derivative Works thereof in any medium, with or without modifications, and
      in Source or Object form, provided that You meet the following conditions:

      (a) You must give any other recipients of the Work or Derivative Works a
          copy of this License; and

      (b) You must cause any modified files to carry prominent notices stating
          that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works that You
          distribute, all copyright, patent, trademark, and attribution notices
          from the Source form of the Work; and

      (d) If the Work includes a "NOTICE" text file, then any Derivative Works
          that You distribute must include a readable copy of the attribution
          notices contained within such NOTICE file.

   5. Submission of Contributions. Unless You explicitly state otherwise, any
      Contribution intentionally submitted for inclusion in the Work by You to
      the Licensor shall be under the terms and conditions of this License.

   6. Trademarks. This License does not grant permission to use the trade names,
      trademarks, service marks, or product names of the Licensor.

   7. Disclaimer of Warranty. Unless required by applicable law or agreed to in
      writing, Licensor provides the Work on an "AS IS" BASIS.

   8. Limitation of Liability. In no event and under no legal theory shall any
      Contributor be liable to You for damages arising as a result of this
      License or out of the use or inability to use the Work.

   9. Accepting Warranty or Additional Liability. While redistributing the Work,
      You may choose to offer support, warranty, indemnity, or other liability
      obligations and/or rights consistent with this License.

   END OF TERMS AND CONDITIONS
```

## MCP Python SDK

VeriLex uses the official Model Context Protocol Python SDK.

- Project: https://github.com/modelcontextprotocol/python-sdk
- License: MIT
- Copyright: Model Context Protocol

```text
MIT License

Copyright (c) Model Context Protocol

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

The MCP server-management secret journal, immutable Keychain reference state
machine, and recovery tests are VeriLex implementations. OpenHands and Cline
were consulted for the general sparse-secret-update problem only; Task 2A did
not copy or adapt their source code. Detailed file-level history is recorded in
`docs/provenance/mcp-server-management.md`.

The final MCP management, per-thread selection, desktop picker, and end-to-end
test harness are VeriLex implementations. The Task 12 provenance audit found no
additional copied or adapted upstream source beyond the Hermes watchdog listed
below.

## Hermes Agent MCP stdio watchdog

VeriLex adapts Hermes Agent's `tools/mcp_stdio_watchdog.py` from commit
`5cc47c994beb243407bb4c8ba47d2ab421cda9cf` for parent-death and process-group
cleanup of local stdio MCP servers. VeriLex changed the CLI contract, environment
filtering, parent validation, signal-race handling, shutdown budget, unreaped
leader handling, packaging integration, and tests.

- Project: https://github.com/NousResearch/hermes-agent
- License: MIT
- Copyright: Copyright (c) 2025 Nous Research

```text
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## jsonschema

VeriLex uses jsonschema 4.26.0 unmodified for local Draft 2020-12 JSON Schema
validation of MCP tool definitions and arguments.

- Project: https://github.com/python-jsonschema/jsonschema
- License: MIT
- Copyright: Copyright (c) 2013 Julian Berman

```text
Copyright (c) 2013 Julian Berman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## python-docx

VeriLex includes python-docx 1.2.0 for the bounded DOCX V1 workflow.

- Project: https://github.com/python-openxml/python-docx
- License: MIT
- Copyright: 2013 Steve Canny
- Complete license: `third_party/licenses/documents/python-docx-LICENSE.txt`

## lxml

VeriLex includes lxml 6.1.2 and its wheel-provided native XML libraries for
bounded OOXML parsing.

- Project: https://github.com/lxml/lxml
- License: BSD-3-Clause, with upstream component notices
- Copyright: 2004 Infrae and contributors
- License files: `third_party/licenses/documents/lxml-LICENSE.txt` and
  `third_party/licenses/documents/lxml-LICENSES.txt`

## Docxodus / Open-Xml-PowerTools

VeriLex evaluates and, after the document engine gate passes, may redistribute or adapt
bounded parts of Docxodus from commit
`b1457bbd78b14177e3e71c739911f1772a6b1b22`. Docxodus continues the
Open-Xml-PowerTools lineage; its MIT license retains both Microsoft Corporation and
John Scrudato IV copyright notices. Task 0 contains no copied production source. The
complete license is distributed at
`third_party/licenses/documents/Docxodus-LICENSE.txt`, and file-level adaptations must
retain inherited headers.

- Project: https://github.com/JSv4/Docxodus
- License: MIT
- Copyright: Microsoft Corporation; 2025-2026 John Scrudato IV

## Adeu

VeriLex evaluates and, after the document engine gate passes, may redistribute or adapt
the local Python document core from Adeu commit
`8a8b103849a3173c07638040c33282731d1af92e`. The intended scope is limited to
safe revision, author/company cleanup, RSID cleanup, custom XML cleanup, and
transactional reporting; Adeu cloud, email, MCP, and Word COM surfaces are not included.
Task 0 contains no copied production source. The complete license is distributed at
`third_party/licenses/documents/Adeu-LICENSE.txt`.

- Project: https://github.com/dealfluence/adeu
- License: MIT
- Copyright: 2026 Dealfluence Oy

## Word QA fonts

The Word delivery gate pins unmodified Noto Sans SC, Noto Serif SC, Carlito, and
Caladea font files from Google Fonts. The font binaries are prepared during the
document-bundle build and are not stored in Git. They remain under SIL Open Font
License 1.1; the license text is at
`qa/documents/fonts/LICENSES/OFL-1.1.txt`.

- Noto Sans SC: Copyright 2014-2021 Adobe; Reserved Font Name “Source”.
- Noto Serif SC: Copyright 2012 Google Inc.
- Carlito: Copyright 2013 The Carlito Project Authors; Reserved Font Name “Carlito”.
- Caladea: Copyright 2012 The Caladea Project Authors.

## LibreOffice

VeriLex's macOS Documents bundle includes an unmodified LibreOffice 26.2.4
application solely as an isolated DOCX-to-PDF process. The generated component
bundle retains LibreOffice's complete upstream LICENSE, LICENSE.html, and NOTICE.
LibreOffice is available under MPL-2.0 and LGPLv3+ terms; bundled dependencies
retain the terms listed in those upstream files.

- Project: https://www.libreoffice.org/
- Redistribution summary: `third_party/licenses/documents/LibreOffice-REDISTRIBUTION.txt`

## pypdfium2 / PDFium

The independent document-rasterizer uses pypdfium2 5.11.0 with its fixed
macOS arm64 PDFium binary. It is not imported by the main Runtime. The generated
component bundle retains Apache-2.0, BSD-3-Clause, PDFium, and all wheel-provided
BUILD_LICENSES files.

- Project: https://github.com/pypdfium2-team/pypdfium2
- Redistribution summary: `third_party/licenses/documents/pypdfium2-REDISTRIBUTION.txt`

## 最高人民法院普通诉讼文书样式（2026-09-09）

`runtime/verilex_runtime/litigation/templates/`三份普通样式仅依据最高人民法院2016官方司法文书正文的栏目和顺序独立建立。具体官方URL、采集时间和正文XML哈希见同目录manifest.json；依据《中华人民共和国著作权法》第五条关于司法性质文件的规定使用官方正文。印刷版式、字段规则、渲染器与检查代码由VeriLex独立实现；不包含第三方实例案情或出版物版式。2025年67类模板尚未纳入。

## 2025民间借贷官方空白文书

来源：深圳市福田区人民法院2025-07-14发布的《部分案件起诉状答辩状示范文本》（https://www.ftcourt.gov.cn/ssfw/sszn/content/post_1577086.html）。仅提取民间借贷空白起诉状正文与表格，权利依据沿官方司法性质文书处理；不包含实例案情、出版社解读、外部字体文件或legal-skills源码/模板资产。原附件实际为OLE DOC，使用LibreOffice转换后摘取；来源、原文件及摘取哈希和过程见scripts/litigation/sources/private-lending-2025.json。保留栏目和表格网格，填充区及格式兼容修正在本项目独立实现；LibreOffice仅用于开发时转换/验证，无新增运行依赖。

2026-09-09：同一哈希固定的民间借贷官方空白表进一步适配个人/组织三种新增组合，沿用上述来源与权利范围；不含第三人，不引入新的上游资产或数据集。

2026-09-09：进一步适配同一官方空白源的个人/组织第三人栏目，新增8个精确组合，未引入新上游代码或实例。official-2025-index.json只记录同份官方文件的67类目录名称及印刷页码，不是运行时样式或实例数据。legal-skills的README/许可冲突尚未解决，仍未复制其资产。

## ima-mcp-server and bundled execution dependencies

VeriLex adapts https://github.com/xuewolai/ima-mcp-server (server version 1.1.0, MIT). Source digest and changes are recorded in runtime/verilex_runtime/knowledge/ima_mcp/UPSTREAM.md; the full upstream MIT license is retained in that directory and bundled. Changes restrict tools to reads, enforce explicit credentials, bound network responses, and sanitize errors.

The bundle includes @modelcontextprotocol/sdk 1.27.1 and official Node.js 22.23.2. Their full licenses, including Node's third-party notices and the bundled JavaScript dependencies' license texts, are included as MCP-SDK-LICENSE.txt, NODE-LICENSE.txt and DEPENDENCY-LICENSE.txt in the ima runtime resources.

The ima official skill 1.1.9 was consulted as API workflow documentation. No official skill source or scripts are redistributed; VeriLex's accompanying instruction skill is independently written for this read-only integration.

## OpenXmlPowerTools（DOCX 切片算法参考，非代码改编）

`runtime/verilex_runtime/documents/replace.py` 的跨 run 保格式替换与
`runtime/verilex_runtime/documents/revisions.py` 的修订接受，参考
Open-Xml-PowerTools 的 TextReplacer 与 RevisionAccepter 公开算法，独立以
Python 实现，未复制其源码或引入 .NET 运行时。
上游：Eric White / Microsoft Corporation，MIT；维护 fork：
https://github.com/Codeuctivity/OpenXmlPowerTools 。
调研与锁定依赖实验：`docs/research/2026-09-15-docx-slices-research.md`。

## Offline Chinese recognition for Word redaction (2026-09-15)

The local file-redaction feature uses spaCy 3.8.16 and zh_core_web_sm 3.8.0
(MIT; https://github.com/explosion/spaCy and
https://github.com/explosion/spacy-models/releases/tag/zh_core_web_sm-3.8.0),
with spacy-pkuseg 1.0.1 (MIT). Installed dependency licenses and notices are
preserved in `third_party/file_redaction/`, including thinc, NumPy, blis,
catalogue, confection, cymem, murmurhash, preshed, srsly, wasabi, weasel,
spaCy legacy/loggers, cloudpathlib, smart-open and supporting utilities.
`runtime/uv.lock` records all exact versions and download hashes. Model training
corpora are not separately obtained or redistributed. The packaged application
includes these notice files and the model package's own license texts.

The occurrence-based Word pipeline independently extends VeriLex's existing
OOXML document slice and preserves its OpenXmlPowerTools algorithm attribution.

## Contract review preview (2026-09-16)

The desktop read-only preview bundles docx-preview 0.4.0 by Volodymyr
Baydalka, Apache-2.0 (https://github.com/VolodymyrBaydalka/docxjs).
Its transitive ZIP dependency is JSZip 3.10.2, used under its MIT license
option, and pako 1.0.11 (MIT AND Zlib). Supporting dependencies are lie
3.3.0, immediate 3.0.6, readable-stream 2.3.8, core-util-is 1.0.3, isarray
1.0.0, process-nextick-args 2.0.1, safe-buffer 5.1.2, setimmediate 1.0.5,
string_decoder 1.1.1, util-deprecate 1.0.2 (MIT), and inherits 2.0.4 (ISC).
Their original license texts are preserved in `third_party/licenses/contract-preview/`
and distributed in the application license resources. Exact dependency versions
and integrity hashes are recorded in `package-lock.json`. The application-specific
annotation overlay and selection workflow are independently implemented.

### 定时任务调度结构（2026-09-17）

`runtime/verilex_runtime/scheduling/scheduler.py` 的先认领后启动、重叠处理、补跑和退出清理结构改编自 OpenWorker `coworker/automation/scheduler.py`，提交 `5bc10d928e0b64aae74313349a3b17bd19643ae2`（https://github.com/andrewyng/openworker）。Copyright (c) 2024 Andrew Ng，MIT；完整许可沿用本文件已有 OpenWorker MIT 声明。改动：SQLite持久化、单运行上限、只读Agent、墙钟上限、失败留档和50条历史清理。

## Litigation document editor

Tiptap 3.31.3 and ProseMirror are unmodified MIT-licensed npm dependencies.
The exact dependency graph is pinned in package-lock.json. No cloud editor,
Tiptap Pro extension or commercial conversion service is included.
Licenses and package versions for the bundled editor family:

- `@tiptap/core@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-core-LICENSE`
- `@tiptap/extension-blockquote@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-blockquote-LICENSE`
- `@tiptap/extension-bold@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-bold-LICENSE`
- `@tiptap/extension-bubble-menu@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-bubble-menu-LICENSE`
- `@tiptap/extension-bullet-list@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-bullet-list-LICENSE`
- `@tiptap/extension-code@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-code-LICENSE`
- `@tiptap/extension-code-block@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-code-block-LICENSE`
- `@tiptap/extension-document@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-document-LICENSE`
- `@tiptap/extension-dropcursor@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-dropcursor-LICENSE`
- `@tiptap/extension-floating-menu@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-floating-menu-LICENSE`
- `@tiptap/extension-gapcursor@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-gapcursor-LICENSE`
- `@tiptap/extension-hard-break@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-hard-break-LICENSE`
- `@tiptap/extension-heading@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-heading-LICENSE`
- `@tiptap/extension-horizontal-rule@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-horizontal-rule-LICENSE`
- `@tiptap/extension-italic@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-italic-LICENSE`
- `@tiptap/extension-link@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-link-LICENSE`
- `@tiptap/extension-list@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-list-LICENSE`
- `@tiptap/extension-list-item@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-list-item-LICENSE`
- `@tiptap/extension-list-keymap@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-list-keymap-LICENSE`
- `@tiptap/extension-ordered-list@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-ordered-list-LICENSE`
- `@tiptap/extension-paragraph@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-paragraph-LICENSE`
- `@tiptap/extension-strike@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-strike-LICENSE`
- `@tiptap/extension-table@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-table-LICENSE`
- `@tiptap/extension-text@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-text-LICENSE`
- `@tiptap/extension-text-align@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-text-align-LICENSE`
- `@tiptap/extension-text-style@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-text-style-LICENSE`
- `@tiptap/extension-underline@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extension-underline-LICENSE`
- `@tiptap/extensions@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-extensions-LICENSE`
- `@tiptap/pm@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-pm-LICENSE`
- `@tiptap/react@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-react-LICENSE`
- `@tiptap/starter-kit@3.31.3`: MIT; `third_party/licenses/litigation-editor/tiptap-starter-kit-LICENSE`
- `prosemirror-changeset@2.4.4`: MIT; `third_party/licenses/litigation-editor/prosemirror-changeset-LICENSE`
- `prosemirror-commands@1.7.2`: MIT; `third_party/licenses/litigation-editor/prosemirror-commands-LICENSE`
- `prosemirror-dropcursor@1.8.4`: MIT; `third_party/licenses/litigation-editor/prosemirror-dropcursor-LICENSE`
- `prosemirror-gapcursor@1.4.1`: MIT; `third_party/licenses/litigation-editor/prosemirror-gapcursor-LICENSE`
- `prosemirror-history@1.5.1`: MIT; `third_party/licenses/litigation-editor/prosemirror-history-LICENSE`
- `prosemirror-inputrules@1.5.1`: MIT; `third_party/licenses/litigation-editor/prosemirror-inputrules-LICENSE`
- `prosemirror-keymap@1.2.3`: MIT; `third_party/licenses/litigation-editor/prosemirror-keymap-LICENSE`
- `prosemirror-model@1.25.12`: MIT; `third_party/licenses/litigation-editor/prosemirror-model-LICENSE`
- `prosemirror-schema-list@1.5.1`: MIT; `third_party/licenses/litigation-editor/prosemirror-schema-list-LICENSE`
- `prosemirror-state@1.4.4`: MIT; `third_party/licenses/litigation-editor/prosemirror-state-LICENSE`
- `prosemirror-tables@1.8.5`: MIT; `third_party/licenses/litigation-editor/prosemirror-tables-LICENSE`
- `prosemirror-transform@1.12.2`: MIT; `third_party/licenses/litigation-editor/prosemirror-transform-LICENSE`
- `prosemirror-view@1.42.6`: MIT; `third_party/licenses/litigation-editor/prosemirror-view-LICENSE`

## Litigation visualization rendering core

Copyright (c) 2026 缪奇川 (Miao Qichuan), MIT. Source: https://github.com/MiaoQichuan/new-litigation-visualization at ba32e9e054c2cd5181cc78bd6abe09cf3d4c6352. Selected timeline, chronicle, relation, confrontation and redraw SVG renderers, geometry and style tokens are in runtime/verilex_runtime/litigation/visual_vendor with LICENSE and a per-file provenance manifest. Imports were adapted for packaging. No case examples, templates or legal data were copied.

## Evidence booklet PDF and fonts

ReportLab 4.4.4, Copyright (c) 2000–2024 ReportLab Inc., BSD-3-Clause; pinned in runtime/uv.lock. Its license is included in third_party/licenses/litigation-editor/reportlab-LICENSE. Optional ReportLab font files are not collected.

Noto Sans SC, Copyright 2014–2021 Adobe, Reserved Font Name “Source”, SIL Open Font License 1.1. The unmodified font is bundled for Chinese PDF subsetting and embedding; exact source revision and SHA-256 are in runtime/verilex_runtime/litigation/fonts/provenance.json. The full OFL is distributed beside the font and in third_party/licenses/litigation-editor/NotoSansSC-OFL.txt. No system font installation or external font service is used.

## Interactive terminal (2026-10-12)

- @xterm/xterm 6.0.0 and @xterm/addon-fit 0.11.0: MIT, https://github.com/xtermjs/xterm.js . Used directly for terminal rendering and size fitting. Full notices are retained in `third_party/licenses/terminal/` and bundled under `licenses/terminal/`.

## commands-shortcuts（2026-10-12）

有限 macOS 系统唤起注册适配 [tauri-apps/global-hotkey](https://github.com/tauri-apps/global-hotkey) 的 `src/platform_impl/macos/mod.rs` 与 Carbon FFI 声明，检视 dev 于2026-10-12，采用 MIT 许可。Copyright (c) 2022-2022 Tauri Programme within The Commons Conservancy。仅保留一个用户主动启用的固定唤起键、主线程注册/注销及回调；未引入媒体键监听或跨平台管理器。完整许可证在 `third_party/licenses/commands-shortcuts/global-hotkey-MIT.txt`，随应用打包。React命令表与面板独立实现。

## QuillPDF PDF page plugin (2026-10-12)

- Source: https://github.com/PurpleDirective/quillpdf-mcp, commit `6ee740498584aa325b826490ed830b9625e71b5c`; MIT, Copyright (c) 2026 Purple Directive.
- Adapted `src/core.ts` merge, splitByRanges, extractPages, pageCount and their loading/range helpers into `runtime/verilex_runtime/pdf_plugin/core.ts`. All other operations omitted; filesystem/MCP adapters independently scoped for VeriLex, no force overwrite. License: `third_party/licenses/pdf-plugin/quillpdf-MIT.txt`, also shipped beside the plugin.
- `pdf-lib` 1.17.1 (MIT) and actual bundled transitive licenses are emitted by `desktop/scripts/build-pdf-plugin.mjs` into `DEPENDENCY-LICENSE.txt` and included in the app. Node and MCP SDK use the existing ima runtime distribution and license bundle. No qpdf WASM, OCR or redaction dependency added.

2026-10-25: litigation evidence assembly also invokes these same QuillPDF functions through a local job adapter (`pdf_plugin/evidence.mjs`). Image embedding uses the existing MIT pdf-lib 1.17.1 API. Office conversion invokes a separately installed LibreOffice; no LibreOffice distribution or new upstream MCP implementation is bundled.

## PowerPoint local plugin (2026-10-12)

- Office-PowerPoint-MCP-Server — Copyright (c) 2025 GongRzhe, MIT. https://github.com/GongRzhe/Office-PowerPoint-MCP-Server , revision `3631ba2ec0c24504476f78bf74d329c9be11caaa`. Selected in-memory functions from `utils/presentation_utils.py` and `utils/content_utils.py` adapted in `runtime/verilex_runtime/ppt_plugin/vendor/primitives.py`; exact inputs/hashes and modifications in `provenance.json`, license in `vendor/LICENSE`. No upstream network/template-search/server tool is exposed.
- python-pptx 1.0.2 — MIT, https://github.com/scanny/python-pptx . License retained as `vendor/LICENSE.python-pptx`.
- XlsxWriter — BSD-2-Clause, https://github.com/jmcnamara/XlsxWriter , transitive python-pptx dependency (version fixed by runtime/uv.lock). License retained as `vendor/LICENSE.XlsxWriter`. All three licenses are collected into the runtime bundle.

## Excel local plugin (2026-10-12)

Adapted domain modules from [Haris Musa / excel-mcp-server](https://github.com/haris-musa/excel-mcp-server), v1.1.2, commit 3ced000247769d3d9ae8d17e99d45655766b488a, MIT, Copyright (c) 2025–2026 Haris Musa. Adaptations of cells, refs, values, formulas and errors include explicit cell input types and local reference target checks; original license and source hashes in runtime/verilex_runtime/excel_plugin/vendor. The separate VeriLex adapter adds bounded folder access and new-file delivery; it does not distribute or claim the upstream 2.x formula engine.

openpyxl 3.1.5 and et-xmlfile 2.0.0 are MIT-licensed (Copyright (c) 2010 openpyxl); license copies in third_party/licenses/excel-plugin. et-xmlfile also ships its Python license notice, retained there.

## Model provider brand logos

- Source: [Lobe Icons](https://github.com/lobehub/lobe-icons), `@lobehub/icons-static-svg` 1.95.1, commit `49a2130df7bfa5eb1b088261bff20a37e2967789`.
- License: MIT; Copyright (c) 2023 LobeHub. Full text: `third_party/licenses/model-logos/lobe-icons-MIT.txt`.
- Unmodified local SVGs: DeepSeek, Qwen, Zhipu, Kimi, Doubao, MiniMax and Ollama, under `desktop/src/assets/model-logos/`. No runtime dependency or CDN request. Brand names and marks belong to their respective owners.

### Embedded contract Word editor (2026-10-22)

The contract workspace now uses the same pinned MIT Docxodus browser bundle for
whole-document editing, including its DocxEditor session and pagination engine.
`desktop/src/documents/word-editor.js` independently supplies Chinese controls,
CSP-safe HTML/style handling and a version-guarded adapter for flushing focused
text before saving. The full resource-tree digest is checked before staging.
License: `third_party/licenses/documents/Docxodus-LICENSE.txt`.

The bundled Docxodus editor includes `@atlaskit/pragmatic-drag-and-drop` and
`@atlaskit/pragmatic-drag-and-drop-auto-scroll` (Apache-2.0), `@babel/runtime`,
`bind-event-listener`, and `raf-schd` (MIT). Their unmodified license files are
packaged in `licenses/word-editor` from `third_party/licenses/word-editor`.

VeriLex's reviewed local source patch `desktop/patches/docxodus-keyboard.patch`
adds tracked paragraph-boundary and table insertion editing, safe
coalescing of the current author's paragraph-format revisions, explicit
paragraph indentation/spacing overrides, and reversible table structure edits
including cell splitting. Font-family writes also set the OOXML East Asian font
slot so Chinese pasted text retains its selected font. The upstream
revision and license remain unchanged; the patch and rebuilt renderer have
separate integrity hashes in the source and renderer locks.
