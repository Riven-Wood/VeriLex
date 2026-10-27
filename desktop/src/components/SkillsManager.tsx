import { useCallback, useEffect, useRef, useState } from "react";
import { FolderOpen, LoaderCircle, Trash2, Wrench } from "lucide-react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DetailDialog } from "./DetailDialog";
import type { SkillSummary } from "../runtime/contracts";
import { skillCategories, skillCategory, skillCategoryLabel } from "./skillCategories";
import "./SkillsManager.css";
import {
  RuntimeApiError,
  deleteSkill,
  listSkills,
  revealSkill,
  setSkillEnabled,
} from "../runtime/api";

const CREATE_HINT = "想新建技能？在对话里说『帮我创建一个技能：…』";

function errorMessage(error: unknown): string {
  if (error instanceof RuntimeApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "操作失败，请重试。";
}

export function SkillsManager({ query = "" }: { query?: string } = {}) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const detailTrigger = useRef<HTMLButtonElement | null>(null);
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [armedName, setArmedName] = useState<string | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState("all");

  useEffect(() => {
    setArmedName(null);
    setExpandedName(null);
  }, [query, selectedCategory]);

  const refresh = useCallback(async () => {
    try {
      const records = await listSkills();
      setSkills(Array.isArray(records) ? records : []);
      setLoadError(null);
    } catch (error) {
      setLoadError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const toggleEnabled = useCallback(
    async (skill: SkillSummary) => {
      setActionError(null);
      setBusyName(skill.name);
      try {
        await setSkillEnabled(skill.name, !skill.enabled);
        await refresh();
      } catch (error) {
        setActionError(errorMessage(error));
      } finally {
        setBusyName(null);
      }
    },
    [refresh],
  );

  const reveal = useCallback(async (skill: SkillSummary) => {
    setActionError(null);
    setBusyName(skill.name);
    try {
      await revealSkill(skill.name);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusyName(null);
    }
  }, []);

  const remove = useCallback(
    async (skill: SkillSummary) => {
      if (armedName !== skill.name) {
        setArmedName(skill.name);
        return;
      }
      setArmedName(null);
      setActionError(null);
      setBusyName(skill.name);
      try {
        await deleteSkill(skill.name);
        await refresh();
      } catch (error) {
        setActionError(errorMessage(error));
      } finally {
        setBusyName(null);
      }
    },
    [armedName, refresh],
  );

  if (loading) {
    return (
      <p className="mcp-loading">
        <LoaderCircle className="attachment-spinner" aria-hidden="true" size={16} /> 正在加载…
      </p>
    );
  }

  const enabledCount = skills.filter((skill) => skill.enabled).length;
  const banner = actionError ?? loadError;
  const counts = new Map<string, number>();
  for (const skill of skills) {
    const category = skillCategory(skill);
    counts.set(category, (counts.get(category) ?? 0) + 1);
  }
  // Keep an emptied selected category visible so deleting its last item does
  // not unexpectedly switch the user's context back to the full collection.
  const categories = skillCategories.filter(([id]) => counts.has(id) || selectedCategory === id);
  const visibleSkills = skills.filter(skill =>
    (selectedCategory === "all" || skillCategory(skill) === selectedCategory) &&
    skill.name.toLocaleLowerCase("zh-CN").includes(query.trim().toLocaleLowerCase("zh-CN")));

  const selectedSkill = skills.find(skill => skill.name === expandedName);
  return (
    <div className="skills-manager">
      {banner ? (
        <p className="skills-manager-error" role="alert">
          {banner}
        </p>
      ) : null}
      <div className="skills-manager-meta">
        <p className="skills-manager-count">
          {`本机 ${skills.length} 个技能，${enabledCount} 个启用`}
        </p>
        <p className="skills-manager-hint">{CREATE_HINT}</p>
      </div>
      {!loadError && skills.length > 0 ? <div className="skills-category-filters" role="group" aria-label="技能用途分类">
        <button type="button" aria-pressed={selectedCategory === "all"} onClick={() => setSelectedCategory("all")}>
          全部（{skills.length}）
        </button>
        {categories.map(([id, label]) => <button key={id} type="button" aria-pressed={selectedCategory === id}
          onClick={() => setSelectedCategory(id)}>{label}（{counts.get(id) ?? 0}）</button>)}
      </div> : null}
      {selectedCategory !== "all" ? <p className="skills-category-result" role="status">
        {skillCategories.find(([id]) => id === selectedCategory)?.[1]} · 显示 {visibleSkills.length} 项
      </p> : null}
      {(query.trim() || selectedCategory !== "all") && visibleSkills.length === 0 && !loadError ? <p className="mcp-empty">{query.trim() ? "没有找到匹配的名称" : "此分类暂无技能"}</p> : null}
      {visibleSkills.length === 0 ? null : (
        <ul className="mcp-list skills-manager-list" aria-label="技能列表">
          {visibleSkills.map((skill) => {
            const armed = armedName === skill.name;
            const busy = busyName === skill.name;
            return (
              <li key={skill.name}>
                <div className="mcp-server-row skills-manager-row">
                  <span className="mcp-server-icon">
                    <Wrench aria-hidden="true" size={15} />
                  </span>
                  <div className="mcp-server-copy">
                    <strong
                      className={
                        skill.enabled ? undefined : "skills-manager-muted"
                      }
                    >
                      {skill.name}
                    </strong>
                    <span className="skills-category-label">{skillCategoryLabel(skill)}</span>
                    {skill.valid ? (
                      <small className="skills-manager-description">{skill.description}</small>
                    ) : (
                      <small>
                        <span className="skills-manager-invalid">无法解析</span>
                        {skill.error ? ` ${skill.error}` : ""}
                      </small>
                    )}
                  </div>
                  {skill.valid ? (
                    <>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={skill.enabled}
                        aria-label={`启用技能 ${skill.name}`}
                        className="skills-manager-switch"
                        disabled={busy}
                        onClick={() => void toggleEnabled(skill)}
                      >
                        <span
                          className={
                            skill.enabled
                              ? "skills-manager-switch-on"
                              : "skills-manager-switch-off"
                          }
                          aria-hidden="true"
                        />
                      </button>
                      <button
                        type="button"
                        className="skills-manager-link"
                        onClick={event => {
                          detailTrigger.current = event.currentTarget;
                          setExpandedName(skill.name);
                        }}
                      >
                        查看
                      </button>
                      <button
                        type="button"
                        className="skills-manager-link"
                        disabled={busy}
                        onClick={() => void reveal(skill)}
                      >
                        <FolderOpen aria-hidden="true" size={13} /> 在访达中显示
                      </button>
                    </>
                  ) : null}
                  <button
                    type="button"
                    className={
                      armed ? "skills-manager-danger" : "skills-manager-link"
                    }
                    disabled={busy}
                    onBlur={armed ? () => setArmedName(null) : undefined}
                    onClick={() => void remove(skill)}
                  >
                    <Trash2 aria-hidden="true" size={13} />
                    {armed ? "确认删除" : "删除"}
                  </button>
                </div>

              </li>
            );
          })}
        </ul>
      )}
      {selectedSkill?.valid && <DetailDialog returnFocus={detailTrigger.current} title={selectedSkill.name} onClose={() => setExpandedName(null)} className="skill-detail-dialog">
        <div className="skill-detail-content">
          <div className="skill-detail-meta"><span>{skillCategoryLabel(selectedSkill)}</span><span>{selectedSkill.enabled ? '已启用' : '已停用'}</span></div>
          <p className="skill-detail-description">{selectedSkill.description}</p>
          <div className="skill-detail-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} disallowedElements={['img', 'a']} unwrapDisallowed>{selectedSkill.instructions.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '')}</ReactMarkdown></div>
        </div>
      </DetailDialog>}
    </div>
  );
}
