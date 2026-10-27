import { useState } from "react";
import { ExternalLink } from "lucide-react";

import { openLegalOfficialPage } from "../runtime/api";

/**
 * 联网渠道失败逃生口（规格硬约束 8）：明确报错之外，提供「用系统浏览器
 * 打开官方页面」出路。Runtime 端点打开的是固定官方首页，不接受任意网址。
 */
export function LegalEscapeHatch() {
  const [failed, setFailed] = useState(false);

  return (
    <div className="legal-escape-hatch">
      {failed ? (
        <span className="legal-escape-error" role="alert">
          官方页面没有打开，请稍后重试。
        </span>
      ) : null}
      <button
        type="button"
        onClick={() => {
          setFailed(false);
          openLegalOfficialPage().catch(() => setFailed(true));
        }}
      >
        <ExternalLink aria-hidden="true" size={13} />
        用系统浏览器打开官方页面
      </button>
    </div>
  );
}
