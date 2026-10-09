import { useState } from "react";
import { pickableScenes } from "./api";
import SceneCard from "./SceneCard";
import type { Collection, Scene } from "./types";

/** Scenes grouped by collection; clicking a card toggles it. */
export default function ScenePicker({
  scenes,
  collections,
  selected,
  onToggle,
  filter,
}: {
  scenes: Scene[];
  collections: Collection[];
  selected: string[];
  onToggle: (scene: Scene) => void;
  filter?: (scene: Scene) => boolean;
}) {
  const [showDeveloper, setShowDeveloper] = useState(false);
  const shown = pickableScenes(scenes, showDeveloper).filter(
    (scene) => !filter || filter(scene),
  );
  const groups = collections
    .map((collection) => ({
      collection,
      items: shown.filter((scene) => scene.collection_id === collection.id),
    }))
    .filter((group) => group.items.length > 0);
  return (
    <div className="space-y-4">
      {groups.length === 0 && (
        <p className="text-xs text-muted-foreground">
          没有可选的影像，请先在“数据”中上传。
        </p>
      )}
      {groups.map(({ collection, items }) => (
        <div key={collection.id}>
          <p className="mb-2 text-xs font-medium">
            {collection.title}
            {collection.place ? (
              <span className="font-normal text-muted-foreground">
                {" · " + collection.place}
              </span>
            ) : null}
          </p>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6">
            {items.map((scene) => (
              <SceneCard
                key={scene.id}
                scene={scene}
                compact
                selected={selected.includes(scene.id)}
                onSelect={() => onToggle(scene)}
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
