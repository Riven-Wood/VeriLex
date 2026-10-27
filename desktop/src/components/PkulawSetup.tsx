import { PKULAW_ENTRY } from '../mcp/catalog';
import { CatalogServiceSetup, type CatalogServiceSetupProps } from './CatalogServiceSetup';

/** Compatibility entry point for the existing nine-service setup. */
export function PkulawSetup(props: Omit<CatalogServiceSetupProps, 'entry'>) {
  return <CatalogServiceSetup {...props} entry={PKULAW_ENTRY} />;
}
