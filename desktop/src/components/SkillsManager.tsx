import { useCallback, useEffect, useRef, useState } from "react";
import { FolderOpen, LoaderCircle, Trash2, Wrench } from "lucide-react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DetailDialog } from "./DetailDialog";
import type { SkillCategory, SkillSummary } from "../runtime/contracts";
import { skillCategories, managedSkillCategory as skillCategory, skillCategoryLabel } from "./skillCategories";
import "./SkillsManager.css";
import {
  RuntimeApiError,
  deleteSkill,
  listSkills,
  revealSkill,
  setSkillEnabled,
  listSkillCategories, createSkillCategory, renameSkillCategory, deleteSkillCategory, setSkillCategory,
} from "../runtime/api";

const CREATE_HINT = "想新建技能？在对话里说『帮我创建一个技能：…』";

function errorMessage(error: unknown): string {
  if (error instanceof RuntimeApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "操作失败，请重试。";
}

export function SkillsManager({ query = "" }: { query?: string } = {}) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [customCategories, setCustomCategories] = useState<SkillCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const detailTrigger = useRef<HTMLButtonElement | null>(null);
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [armedName, setArmedName] = useState<string | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [managingCategories, setManagingCategories] = useState(false);
  const [categoryBusy, setCategoryBusy] = useState(false);
  const [newCategory, setNewCategory] = useState("");
  const [editingCategory, setEditingCategory] = useState<string | null>(null);
  const [categoryName, setCategoryName] = useState("");
  const [deletingCategory, setDeletingCategory] = useState<string | null>(null);
  const categoryTrigger = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    setArmedName(null);
    setExpandedName(null);
  }, [query, selectedCategory]);

  const refresh = useCallback(async () => {
    try {
      const [records, categories] = await Promise.all([listSkills(), listSkillCategories()]);
      setSkills(Array.isArray(records) ? records : []);
      setCustomCategories(categories);
      setLoadError(null);
    } catch (error) {
      setLoadError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  const changeCategory = async (skill: SkillSummary, category: string) => {
    setActionError(null);
    setBusyName(skill.name);
    try {
      await setSkillCategory(skill.name, category === "auto" ? null : category);
      await refresh();
    } catch (error) { setActionError(errorMessage(error)); }
    finally { setBusyName(null); }
  };

  const manageCategory = async (action: () => Promise<unknown>, after: () => void) => {
    setActionError(null);
    setCategoryBusy(true);
    try {
      await action();
      await refresh();
      after();
    } catch (error) { setActionError(errorMessage(error)); }
    finally { setCategoryBusy(false); }
  };

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
  const categoryOptions: readonly (readonly [string, string])[] = [...skillCategories, ...customCategories.map(item => [item.id, item.name] as const)];
  const categories = categoryOptions.filter(([id]) => counts.has(id) || selectedCategory === id || id.startsWith("custom:"));
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
      <div className="skills-manager-heading">
        <div className="skills-manager-meta">
          <p className="skills-manager-count">
            {`本机 ${skills.length} 个技能，${enabledCount} 个启用`}
          </p>
          <p className="skills-manager-hint">{CREATE_HINT}</p>
        </div>
        {!loadError && <div className="skills-category-toolbar">
          <button ref={categoryTrigger} type="button" className="mcp-secondary-button" disabled={busyName !== null} onClick={() => setManagingCategories(true)}>管理分类</button>
        </div>}
      </div>
      {!loadError && skills.length > 0 ? <div className="skills-category-filters" role="group" aria-label="技能用途分类">
        <button type="button" aria-pressed={selectedCategory === "all"} onClick={() => setSelectedCategory("all")}>
          全部（{skills.length}）
        </button>
        {categories.map(([id, label]) => <button key={id} type="button" aria-pressed={selectedCategory === id}
          onClick={() => setSelectedCategory(id)}>{label}（{counts.get(id) ?? 0}）</button>)}
      </div> : null}
      {selectedCategory !== "all" ? <p className="skills-category-result" role="status">
        {categoryOptions.find(([id]) => id === selectedCategory)?.[1]} · 显示 {visibleSkills.length} 项
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
                    <span className="skills-category-label">{skillCategoryLabel(skill, customCategories)}</span>
                    {skill.valid ? (
                      <small className="skills-manager-description">{skill.description}</small>
                    ) : (
                      <small>
                        <span className="skills-manager-invalid">无法解析</span>
                        {skill.error ? ` ${skill.error}` : ""}
                      </small>
                    )}
                  </div>
                  <label className="skills-category-select">
                    <span>分类</span>
                    <select aria-label={`选择技能 ${skill.name} 的分类`} value={skill.categoryOverride ?? "auto"} disabled={busyName !== null}
                      onChange={event => void changeCategory(skill, event.target.value)}>
                      <option value="auto">自动分类</option>
                      {categoryOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                    </select>
                  </label>
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
          <div className="skill-detail-meta"><span>{skillCategoryLabel(selectedSkill, customCategories)}</span><span>{selectedSkill.enabled ? '已启用' : '已停用'}</span></div>
          <p className="skill-detail-description">{selectedSkill.description}</p>
          <div className="skill-detail-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} disallowedElements={['img', 'a']} unwrapDisallowed>{selectedSkill.instructions.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, '')}</ReactMarkdown></div>
        </div>
      </DetailDialog>}
      {managingCategories && <DetailDialog title="管理技能分类" className="skill-category-dialog" busy={categoryBusy}
        returnFocus={categoryTrigger.current} onClose={() => {setManagingCategories(false); setActionError(null);}}>
        <div className="skill-category-content">
          <p>新建分类后，可在每个技能旁选择。删除分类后，其中的技能会恢复自动分类。</p>
          {actionError && <p role="alert" className="skills-manager-error">{actionError}</p>}
          <form className="skill-category-form" onSubmit={event => {event.preventDefault(); const name = newCategory.trim(); if (name) void manageCategory(() => createSkillCategory(name), () => setNewCategory(""));}}>
            <label htmlFor="new-skill-category">新分类名称</label>
            <div><input id="new-skill-category" value={newCategory} maxLength={64} disabled={categoryBusy} onChange={event => setNewCategory(event.target.value)} placeholder="例如：我的办案流程" />
              <button type="submit" className="mcp-add" disabled={categoryBusy || !newCategory.trim()}>新建分类</button></div>
          </form>
          <h3>自定义分类</h3>
          {customCategories.length === 0 ? <p className="skill-category-empty">还没有自定义分类</p> : <ul className="skill-category-list">
            {customCategories.map(category => <li key={category.id}>
              {editingCategory === category.id ? <form onSubmit={event => {event.preventDefault(); const name = categoryName.trim(); if (name) void manageCategory(() => renameSkillCategory(category.id, name), () => setEditingCategory(null));}}>
                <input aria-label={`修改分类 ${category.name} 的名称`} value={categoryName} maxLength={64} disabled={categoryBusy} onChange={event => setCategoryName(event.target.value)} />
                <button type="submit" disabled={categoryBusy || !categoryName.trim()}>保存</button>
                <button type="button" disabled={categoryBusy} onClick={() => setEditingCategory(null)}>取消</button>
              </form> : <><span>{category.name} <small>{counts.get(category.id) ?? 0} 个技能</small></span>
                <div><button type="button" disabled={categoryBusy} aria-label={`重命名分类 ${category.name}`} onClick={() => {setDeletingCategory(null); setEditingCategory(category.id); setCategoryName(category.name);}}>重命名</button>
                  <button type="button" disabled={categoryBusy} aria-label={`${deletingCategory === category.id ? "确认删除" : "删除"}分类 ${category.name}`}
                    onClick={() => deletingCategory !== category.id ? setDeletingCategory(category.id) : void manageCategory(() => deleteSkillCategory(category.id), () => {setDeletingCategory(null); if (selectedCategory === category.id) setSelectedCategory("all");})}>
                    {deletingCategory === category.id ? "确认删除" : "删除"}</button></div></>}
            </li>)}
          </ul>}
        </div>
      </DetailDialog>}
    </div>
  );
}
