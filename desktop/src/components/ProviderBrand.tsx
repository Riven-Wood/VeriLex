// Brand SVGs: @lobehub/icons-static-svg 1.95.1, MIT (c) 2023 LobeHub.
// Source and license: src/assets/model-logos/README.md and THIRD_PARTY_NOTICES.md.
import { Server } from 'lucide-react';
import deepseek from '../assets/model-logos/deepseek-color.svg';
import qwen from '../assets/model-logos/qwen-color.svg';
import glm from '../assets/model-logos/zhipu-color.svg';
import kimi from '../assets/model-logos/kimi.svg';
import doubao from '../assets/model-logos/doubao-color.svg';
import minimax from '../assets/model-logos/minimax-color.svg';
import ollama from '../assets/model-logos/ollama.svg';
import { PROVIDER_PRESETS } from '../providers/presets';
import type { ProviderRecord } from '../runtime/contracts';

const logos: Record<string, string> = { deepseek, qwen, glm, kimi, doubao, minimax, ollama };
export function providerBrandId(provider: Pick<ProviderRecord, 'kind' | 'base_url'>) {
  if (provider.kind === 'ollama') return 'ollama';
  return PROVIDER_PRESETS.find(preset => preset.baseUrl.replace(/\/+$/, '') === provider.base_url.replace(/\/+$/, ''))?.id;
}
export function ProviderBrand({id}: {id?: string}) {
  return id && logos[id]
    ? <img className="provider-brand-logo" src={logos[id]} alt="" aria-hidden="true" draggable={false} />
    : <Server size={21} aria-hidden="true" />;
}
