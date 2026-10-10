import { useState } from "react";
import ItemCard from "./ItemCard";
import type { CatalogCollection, CatalogItem } from "./types";

/** Data a scenario can use, grouped by collection; clicking a card toggles it. */
export default function ItemPicker({
  items,
  collections,
  selected,
  onToggle,
}: {
  items: CatalogItem[];
  collections: CatalogCollection[];
  selected: string[];
  onToggle: (item: CatalogItem) => void;
}) {
  const [showDeveloper, setShowDeveloper] = useState(false);
  const shown = items.filter(
    (item) =>
      item.usable_in.length > 0 &&
      (showDeveloper || item.audience !== "developer"),
  );
  const groups = collections
    .map((collection) => ({
      collection,
      members: shown.filter((item) => item.collection_id === collection.id),
    }))
    .filter((group) => group.members.length > 0);
  return (
    <div className="space-y-4">
      {groups.length === 0 && (
        <p className="text-xs text-muted-foreground">
          没有可选的数据，请先在“数据”中上传。
        </p>
      )}
      {groups.map(({ collection, members }) => (
        <div key={collection.id}>
          <p className="mb-2 text-xs font-medium">
            {collection.title}
            <span className="font-normal text-muted-foreground">
              {" · " + collection.type_label}
              {collection.place ? " · " + collection.place : ""}
            </span>
          </p>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
            {members.map((item) => (
              <ItemCard
                key={item.id}
                item={item}
                compact
                selected={selected.includes(item.id)}
                onSelect={() => onToggle(item)}
              />
            ))}
          </div>
        </div>
      ))}
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={showDeveloper}
          onChange={(event) => setShowDeveloper(event.target.checked)}
        />
        显示开发者数据（合成测试栅格、测试上传）
      </label>
    </div>
  );
}
