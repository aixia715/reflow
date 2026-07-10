document.addEventListener('alpine:init', () => {
  Alpine.data('hardChangeImages', () => ({
    pending: [],
    _k: 0,
    jobs: 0,
    error: '',
    maxBytes: 10 * 1024 * 1024,
    mimeExt: {
      'image/png': 'png',
      'image/jpeg': 'jpg',
      'image/webp': 'webp',
      'image/gif': 'gif',
    },

    get compressing() {
      return this.jobs > 0;
    },

    formatSize(bytes) {
      return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    },

    async addFiles(list) {
      // FileList 会在重建 input.files 时变化，异步压缩前必须先复制。
      const selected = Array.from(list);
      if (!selected.length) return;
      this.jobs += 1;
      this.error = '';
      try {
        for (const original of selected) {
          try {
            const file = await this.compressIfNeeded(original);
            this.pending.push({
              file,
              url: URL.createObjectURL(file),
              key: this._k++,
              originalSize: original.size,
              compressed: file !== original,
            });
          } catch (err) {
            this.error = err instanceof Error ? err.message : '图片压缩失败，请更换图片后重试';
          }
        }
      } finally {
        this.jobs -= 1;
        this.syncInput();
      }
    },

    async compressIfNeeded(file) {
      if (file.size <= this.maxBytes) return file;
      if (file.type === 'image/gif' && await this.isAnimatedGif(file)) {
        throw new Error(`“${file.name}”是超过 10 MB 的 GIF 动图，浏览器无法在保留动画的同时压缩，请先压缩后再上传`);
      }

      let bitmap;
      try {
        bitmap = await createImageBitmap(file);
      } catch (_) {
        throw new Error(`“${file.name}”无法读取，未能完成压缩`);
      }

      try {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        if (!ctx) throw new Error('浏览器不支持图片压缩');
        let width = bitmap.width;
        let height = bitmap.height;
        const qualities = [0.88, 0.8, 0.72, 0.64, 0.56];

        // 优先降低 WebP 质量；仍超限时再逐步缩小尺寸，避免一次性损失过多细节。
        for (let resizeCount = 0; resizeCount < 24; resizeCount += 1) {
          canvas.width = width;
          canvas.height = height;
          ctx.clearRect(0, 0, width, height);
          ctx.drawImage(bitmap, 0, 0, width, height);
          for (const quality of qualities) {
            const blob = await this.canvasToBlob(canvas, quality);
            if (blob && blob.size < this.maxBytes) {
              const stem = file.name.replace(/\.[^.]*$/, '') || 'image';
              return new File([blob], `${stem}.webp`, {
                type: 'image/webp',
                lastModified: file.lastModified,
              });
            }
          }
          if (width === 1 && height === 1) break;
          width = Math.max(1, Math.floor(width * 0.82));
          height = Math.max(1, Math.floor(height * 0.82));
        }
      } finally {
        bitmap.close();
      }
      throw new Error(`“${file.name}”压缩后仍超过 10 MB，请先压缩后再上传`);
    },

    canvasToBlob(canvas, quality) {
      return new Promise(resolve => canvas.toBlob(resolve, 'image/webp', quality));
    },

    async isAnimatedGif(file) {
      // Canvas 只能保留 GIF 第一帧；不能可靠识别动画时采取保守策略，避免静默丢帧。
      if (typeof ImageDecoder === 'undefined') return true;
      let decoder;
      try {
        decoder = new ImageDecoder({data: await file.arrayBuffer(), type: file.type});
        await decoder.tracks.ready;
        return (decoder.tracks.selectedTrack?.frameCount || 1) > 1;
      } catch (_) {
        return true;
      } finally {
        if (decoder) decoder.close();
      }
    },

    onPaste(e) {
      const items = e.clipboardData ? e.clipboardData.items : [];
      const images = [];
      for (const item of items) {
        if (item.kind === 'file' && this.mimeExt[item.type]) {
          const file = item.getAsFile();
          if (file) images.push(this.ensureNamed(file));
        }
      }
      if (!images.length) return;
      e.preventDefault();
      this.addFiles(images);
    },

    ensureNamed(file) {
      const dot = file.name ? file.name.lastIndexOf('.') : -1;
      if (dot > 0 && dot < file.name.length - 1) return file;
      const ext = this.mimeExt[file.type] || 'png';
      return new File([file], `pasted-${Date.now()}-${this._k++}.${ext}`, {type: file.type});
    },

    syncInput() {
      const transfer = new DataTransfer();
      this.pending.forEach(item => transfer.items.add(item.file));
      this.$refs.fileInput.files = transfer.files;
    },

    remove(index) {
      URL.revokeObjectURL(this.pending[index].url);
      this.pending.splice(index, 1);
      this.syncInput();
    },
  }));
});
