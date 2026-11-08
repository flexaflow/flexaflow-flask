(function () {
	var meta = document.querySelector('meta[name="csrf-token"]');
	var csrfToken = meta ? meta.content : '';

	var nativeFetch = window.fetch;
	window.fetch = function (input, init) {
		init = init || {};
		var method = (init.method || 'GET').toUpperCase();
		if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
			var headers = new Headers(init.headers || {});
			if (!headers.has('X-CSRF-Token')) {
				headers.set('X-CSRF-Token', csrfToken);
			}
			init.headers = headers;
		}
		return nativeFetch.call(this, input, init);
	};

	window.FlexaAdmin = {
		csrfToken: csrfToken,
		uniqueRowId: (function () {
			var counter = 0;
			return function () { counter += 1; return 'n' + Date.now().toString(36) + counter; };
		})()
	};
})();

(function ($) {
	if (!$) { return; }

	$(document).on('click', '.js-add-row', function () {
		var $repeater = $(this).closest('.repeater');
		var html = $repeater.children('template').first().html();
		var rowId = window.FlexaAdmin.uniqueRowId();
		html = html.split('__ROW__').join(rowId);
		$repeater.find('.repeater-rows').first().append(html);
	});

	$(document).on('click', '.js-remove-row', function () {
		$(this).closest('.repeater-row').remove();
	});

	var imagePickTarget = null;
	$(document).on('click', '.js-pick-image', function () {
		imagePickTarget = $(this).closest('.image-field');
		if (typeof window.openMediaPicker === 'function') {
			window.openMediaPicker(function (url) {
				imagePickTarget.find('.js-image-url').val(url).trigger('change');
			});
		}
	});

	$(document).on('click', '.js-clear-image', function () {
		var $field = $(this).closest('.image-field');
		$field.find('.js-image-url').val('').trigger('change');
	});

	$(document).on('change', '.js-image-url', function () {
		var $field = $(this).closest('.image-field');
		var url = $(this).val();
		$field.find('.js-image-preview').attr('src', url).toggle(!!url);
	});

	$(document).on('click', '.js-upload-file', function () {
		$(this).closest('.file-field').find('.js-file-input').trigger('click');
	});
	$(document).on('change', '.js-file-input', function () {
		var $field = $(this).closest('.file-field');
		var file = this.files && this.files[0];
		var $status = $field.find('.js-file-status');
		if (!file) { return; }
		var data = new FormData();
		data.append('file', file);
		$status.text('Uploading ' + file.name + '…');
		fetch('/admin/upload_file', { method: 'POST', body: data })
			.then(function (r) { return r.json(); })
			.then(function (res) {
				if (res.success) {
					$field.find('.js-file-url').val(res.url).trigger('change');
					$status.text('Uploaded ' + res.name + '.');
				} else {
					$status.text(res.error || 'Upload failed.');
				}
			})
			.catch(function () { $status.text('Upload failed. Check your connection and try again.'); });
		this.value = '';
	});
	$(document).on('click', '.js-clear-file', function () {
		var $field = $(this).closest('.file-field');
		$field.find('.js-file-url').val('').trigger('change');
		$field.find('.js-file-status').text('');
	});

	$(document).on('change', '.js-template-switch', function () {
		var $form = $(this).closest('form');
		$form.find('input[name="action"]').val('switch_template');
		$form.trigger('submit');
	});
})(window.jQuery);
