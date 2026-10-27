import { useRef, type KeyboardEvent } from "react";

import { ArrowLeft } from "lucide-react";

import {
  filterSettingsCategories, searchSettings, SETTINGS_CATEGORIES,
  type SettingsCategoryId,
} from "../settings/categories";

interface SettingsNavigationProps {
  selected: SettingsCategoryId;
  query: string;
  onQueryChange: (value: string) => void;
  onSelect: (value: SettingsCategoryId) => void;
  onBack?: () => void;
}

export function SettingsNavigation({
  selected,
  query,
  onQueryChange,
  onSelect,
  onBack,
}: SettingsNavigationProps) {
  const buttonRefs = useRef(new Map<SettingsCategoryId, HTMLButtonElement>());
  const visibleCategories = filterSettingsCategories(query);
  const matches = searchSettings(query);

  const handleCategoryKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
    categoryId: SettingsCategoryId,
  ) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(categoryId);
      return;
    }

    const offset = event.key === "ArrowDown" || event.key === "ArrowRight"
      ? 1
      : event.key === "ArrowUp" || event.key === "ArrowLeft"
        ? -1
        : 0;
    if (!offset || visibleCategories.length === 0) return;

    event.preventDefault();
    const nextIndex = (index + offset + visibleCategories.length) % visibleCategories.length;
    buttonRefs.current.get(visibleCategories[nextIndex].id)?.focus();
  };

  return (
    <nav className="settings-navigation" aria-label="设置分类">
      {onBack && (
        <button
          type="button"
          className="settings-back"
          onClick={onBack}
        >
          <ArrowLeft aria-hidden="true" size={16} />
          <span>返回应用</span>
        </button>
      )}
      <label className="settings-search">
        <span>搜索设置</span>
        <input
          type="search"
          value={query}
          placeholder="搜索设置"
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Escape" || !query) return;
            event.preventDefault();
            onQueryChange("");
          }}
        />
      </label>
      {query.trim() && matches.length>0 ? <div className="settings-search-results" aria-label="匹配的设置">{matches.map(entry=><button key={entry.id} onClick={()=>{onSelect(entry.category);onQueryChange(entry.label);}}><strong>{entry.label}</strong><small>{SETTINGS_CATEGORIES.find(c=>c.id===entry.category)?.label}</small></button>)}</div> : null}
      <div className="settings-category-list">
        {visibleCategories.map((category, index) => (
          <button
            key={category.id}
            ref={(node) => {
              if (node) buttonRefs.current.set(category.id, node);
              else buttonRefs.current.delete(category.id);
            }}
            type="button"
            aria-label={category.label}
            aria-current={selected === category.id ? "page" : undefined}
            onClick={() => onSelect(category.id)}
            onKeyDown={(event) => handleCategoryKeyDown(event, index, category.id)}
          >
            <strong>{category.label}</strong>
            <small>{category.description}</small>
          </button>
        ))}
      </div>
      {visibleCategories.length === 0 ? (
        <p className="settings-navigation-empty" role="status" aria-live="polite">
          没有匹配的设置
        </p>
      ) : null}
    </nav>
  );
}
